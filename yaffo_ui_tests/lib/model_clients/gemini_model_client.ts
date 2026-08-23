import {generateText} from "ai";
import {createGoogleGenerativeAI} from "@ai-sdk/google";
import type {z} from "zod";
import {
    ModelClient,
    ModelResponse,
} from "@lib/model_clients/model_client.interface";
import {RawToolDefinition} from "@lib/tool_providers/toolprovider.types";
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
        let httpResponse: Response | undefined;
        let url: RequestInfo | URL | undefined;
        let options: RequestInit | undefined;
        try {
            const google = createGoogleGenerativeAI({
                apiKey: process.env.GOOGLE_GENERATIVE_AI_API_KEY,
                fetch: async (requestUrl, requestOptions) => {
                    url = requestUrl;
                    options = requestOptions;
                    const res = await fetch(requestUrl, requestOptions);
                    httpResponse = res.clone();
                    return res;
                }
            });
            result = await generateText({
                model: google(this.model),
                system: this.systemPrompt,
                messages: this.buildOrderedMessages(),
                tools: this.sdkTools,
                maxOutputTokens: 8192,
            });

            cacheUsage = this.trackUsage(result.usage);
            const response = this.convertToModelResponse(result);
            if (result.text) {
                console.log(`   🤖 ${result.text.slice(0, 200)}`);
            }
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