import {generateText} from "ai";
import type {LanguageModel} from "ai";
import {createOpenAI} from "@ai-sdk/openai";
import {createDeepSeek} from "@ai-sdk/deepseek";
import {createXai} from "@ai-sdk/xai";
import {createOpenAICompatible} from "@ai-sdk/openai-compatible";
import type {z} from "zod";
import {
    ModelClient,
    ModelResponse,
    supportsVision,
} from "@lib/model_clients/model_client.interface";
import type {ModelAlias} from "@lib/model_clients/model_client.interface";
import {RawToolDefinition} from "@lib/tool_providers/toolprovider.types";
import {CacheUsage} from "@lib/model_clients/model_client.types";
import {inspect} from "node:util";
import {BaseModelClient} from "@lib/model_clients/base_model_client";

export type OpenAiModelAlias = "gpt-5.6-sol" | "gpt-5.6-terra" | "gpt-5.6-luna";
export type DeepSeekModelAlias =
    | "deepseek-v4-pro"
    | "deepseek-v4-flash"
    | "deepseek-v4-flash-vision-exp";
export type MoonshotModelAlias = "kimi-k3";
export type XaiModelAlias = "grok-4.5" | "grok-4.3";

export type SdkProvider = "openai" | "deepseek" | "moonshot" | "xai";
export type SdkProviderModelAlias =
    | OpenAiModelAlias
    | DeepSeekModelAlias
    | MoonshotModelAlias
    | XaiModelAlias;

interface SdkProviderSpec {
    /** Prefix for per-call API log filenames (matches "claude"/"gemini" convention). */
    logPrefix: string;
    /** Human-readable name for error messages. */
    label: string;
}

const PROVIDER_SPECS: Record<SdkProvider, SdkProviderSpec> = {
    openai: {logPrefix: "openai", label: "OpenAI"},
    deepseek: {logPrefix: "deepseek", label: "DeepSeek"},
    moonshot: {logPrefix: "kimi", label: "Kimi (Moonshot)"},
    xai: {logPrefix: "grok", label: "Grok (xAI)"},
};

type FetchLike = typeof globalThis.fetch;

/**
 * Build an AI SDK LanguageModel for one of the OpenAI-style providers. Shared
 * by the heal client below and by preflight, so provider wiring lives in one
 * place. Kimi has no dedicated AI SDK package; it goes through the
 * openai-compatible provider against Moonshot's endpoint.
 */
export function createSdkProviderModel(
    provider: SdkProvider,
    model: string,
    fetchImpl?: FetchLike,
): LanguageModel {
    switch (provider) {
        case "openai":
            return createOpenAI({apiKey: process.env.OPENAI_API_KEY, fetch: fetchImpl})(model);
        case "deepseek":
            // The dedicated @ai-sdk/deepseek provider silently drops image parts:
            // a request carrying one arrives at the API as a plain string, so a
            // vision model answers as though nothing was attached. DeepSeek's API is
            // OpenAI-compatible and that provider does forward images, so vision
            // models route through it. Verified against deepseek-v4-flash-vision-exp.
            // See https://api-docs.deepseek.com/guides/vision/
            return supportsVision(model as ModelAlias)
                ? createOpenAICompatible({
                    name: "deepseek",
                    baseURL: "https://api.deepseek.com/v1",
                    apiKey: process.env.DEEPSEEK_API_KEY,
                    fetch: fetchImpl,
                })(model)
                : createDeepSeek({apiKey: process.env.DEEPSEEK_API_KEY, fetch: fetchImpl})(model);
        case "xai":
            return createXai({apiKey: process.env.XAI_API_KEY, fetch: fetchImpl})(model);
        case "moonshot":
            return createOpenAICompatible({
                name: "moonshot",
                baseURL: "https://api.moonshot.ai/v1",
                apiKey: process.env.MOONSHOT_API_KEY,
                fetch: fetchImpl,
            })(model);
    }
}

export class SdkProviderModelClient extends BaseModelClient {
    readonly logPrefix: string;
    private readonly spec: SdkProviderSpec;

    constructor(
        private readonly provider: SdkProvider,
        runLogDir: string,
        public override readonly model: SdkProviderModelAlias,
        systemPrompt: string,
        rawTools: RawToolDefinition[],
        outputSchema: z.ZodType,
    ) {
        super(runLogDir, model, systemPrompt, rawTools, outputSchema);
        this.spec = PROVIDER_SPECS[provider];
        this.logPrefix = this.spec.logPrefix;
    }

    public async callModelApi(): Promise<ModelResponse | undefined> {
        const timestamp = new Date();
        let result: Awaited<ReturnType<typeof generateText>> | undefined;
        let cacheUsage: CacheUsage | undefined;
        let httpResponse: Response | undefined;
        let url: RequestInfo | URL | undefined;
        let options: RequestInit | undefined;
        try {
            const model = createSdkProviderModel(this.provider, this.model, async (requestUrl, requestOptions) => {
                url = requestUrl;
                options = requestOptions;
                const res = await fetch(requestUrl, requestOptions);
                httpResponse = res.clone();
                return res;
            });
            result = await generateText({
                model,
                system: this.systemPrompt,
                messages: this.buildOrderedMessages(),
                tools: this.sdkTools,
                // Covers hidden reasoning tokens as well as the visible response on a
                // reasoning model, and reasoning is spent first. Raise it per turn with
                // setMaxOutputTokens when a lot of text has to come back.
                maxOutputTokens: this.maxOutputTokens,
            });

            cacheUsage = this.trackUsage(result.usage);
            const response = this.convertToModelResponse(result);
            this.logResponsePreview(result.text, result.reasoningText);
            this.storeAssistantMessages(result);
            return response;
        } catch (error) {
            const errorMessage = inspect(error);
            console.error(`Error when calling ${this.spec.label} API: ${errorMessage}`);
            this.lastError = (error instanceof Error ? error.message : String(error)).slice(0, 500);
            return undefined;
        } finally {
            const durationMs = Date.now() - timestamp.getTime();
            const costEstimate = cacheUsage ? this.estimateCost(cacheUsage) : undefined;
            const responseText = await httpResponse?.text();
            this.writeApiLog({
                timestamp: timestamp.toISOString(),
                durationMs,
                request: {
                    url: url,
                    body: options?.body ? await new Response(options.body.toString()).json() : undefined,
                    headers: options?.headers ?? [],
                },
                response: responseText ? JSON.parse(responseText) : undefined,
                success: result != null,
                cacheUsage,
                costEstimate,
            });
            this.apiCallCount += 1;
        }
    }
}

export const sdkProviderModelClientFactory = (
    provider: SdkProvider,
    runLogDir: string,
    model: SdkProviderModelAlias,
    systemPrompt: string,
    rawTools: RawToolDefinition[],
    outputSchema: z.ZodType,
): ModelClient => {
    return new SdkProviderModelClient(
        provider,
        runLogDir,
        model,
        systemPrompt,
        rawTools,
        outputSchema,
    );
};