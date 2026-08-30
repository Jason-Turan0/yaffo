import {generateText} from "ai";
import {createGoogleGenerativeAI} from "@ai-sdk/google";
import type {z} from "zod";
import {
    ModelClient,
    ModelResponse,
} from "@lib/model_clients/model_client.interface";
import {RawToolDefinition} from "@lib/tool_providers/toolprovider.types";
import {recoverMalformedFunctionCall} from "./gemini_recovery";
import {CacheUsage} from "@lib/model_clients/model_client.types";
import {inspect} from "node:util";
import {BaseModelClient} from "@lib/model_clients/base_model_client";

export type GeminiModelAlias =
    | "gemini-2.0-flash"
    | "gemini-2.5-flash"
    | "gemini-2.5-pro"
    | "gemini-3.6-flash";

export class GeminiModelClient extends BaseModelClient {
    readonly logPrefix = "gemini";

    constructor(
        runLogDir: string,
        public override readonly model: GeminiModelAlias,
        systemPrompt: string,
        rawTools: RawToolDefinition[],
        outputSchema: z.ZodType,
    ) {
        super(runLogDir, model, systemPrompt, rawTools, outputSchema);
    }

    public async callModelApi(): Promise<ModelResponse | undefined> {
        const timestamp = new Date();
        let result: Awaited<ReturnType<typeof generateText>> | undefined;
        let cacheUsage: CacheUsage | undefined;
        let rawBody: string | undefined;
        let url: RequestInfo | URL | undefined;
        let options: RequestInit | undefined;
        try {
            const google = createGoogleGenerativeAI({
                apiKey: process.env.GOOGLE_GENERATIVE_AI_API_KEY,
                fetch: async (requestUrl, requestOptions) => {
                    url = requestUrl;
                    options = requestOptions;
                    const res = await fetch(requestUrl, requestOptions);
                    // Read once, here: the body is needed both for the API log and to
                    // recover an answer Gemini discarded, and a Response can only be
                    // consumed a single time.
                    rawBody = await res.clone().text();
                    return res;
                }
            });
            result = await generateText({
                model: google(this.model),
                system: this.systemPrompt,
                messages: this.buildOrderedMessages(),
                tools: this.sdkTools,
                // Covers hidden reasoning as well as the visible response, and reasoning
                // is spent first. Raise it per turn with setMaxOutputTokens when a lot
                // of text has to come back — a docs generate turn returns two whole
                // files and was observed at 91% of the old hardcoded 8192.
                maxOutputTokens: this.maxOutputTokens,
            });

            cacheUsage = this.trackUsage(result.usage);
            const response = this.convertToModelResponse(result);

            // Gemini drops the answer when it mistakes a JSON reply for a function call
            // and cannot parse it. The text is still in the raw body; take it rather
            // than reporting an empty response and burning a retry.
            if (!response.text?.trim() && !response.toolCalls.length) {
                const recovered = recoverMalformedFunctionCall(rawBody);
                if (recovered) {
                    console.log("   ⤷ recovered an answer Gemini reported as a " +
                        "malformed function call");
                    response.text = recovered;
                }
            }

            this.logResponsePreview(response.text, result.reasoningText);
            this.storeAssistantMessages(result);
            return response;
        } catch (error) {
            const errorMessage = inspect(error);
            console.error(`Error when calling Gemini API: ${errorMessage}`);
            this.lastError = (error instanceof Error ? error.message : String(error)).slice(0, 500);
            return undefined;
        } finally {
            const durationMs = Date.now() - timestamp.getTime();
            const costEstimate = cacheUsage ? this.estimateCost(cacheUsage) : undefined;
            const responseText = rawBody;
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

export const geminiModelClientFactory = (
    runLogDir: string,
    model: GeminiModelAlias,
    systemPrompt: string,
    rawTools: RawToolDefinition[],
    outputSchema: z.ZodType,
): ModelClient => {
    return new GeminiModelClient(
        runLogDir,
        model,
        systemPrompt,
        rawTools,
        outputSchema,
    );
};
