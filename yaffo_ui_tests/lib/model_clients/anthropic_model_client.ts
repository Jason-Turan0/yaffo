import {
    generateText, ToolResultPart, Output, UserModelMessage, ToolModelMessage,
} from "ai";
import {createAnthropic} from "@ai-sdk/anthropic";
import type {z} from "zod";
import type {TextPart} from "ai";
import {
    ModelClient,
    ModelResponse,
    ModelMessage,
} from "@lib/model_clients/model_client.interface";
import {RawToolDefinition} from "@lib/tool_providers/toolprovider.types";
import {CacheUsage} from "@lib/model_clients/model_client.types";
import {inspect} from "node:util";
import _ from 'lodash';
import {BaseModelClient} from "@lib/model_clients/base_model_client";

export type AnthropicModelAlias = "claude-opus-5" | "claude-sonnet-5" | "claude-haiku-4-5";

export class AnthropicModelClient extends BaseModelClient {
    readonly logPrefix = "claude";

    constructor(
        runLogDir: string,
        public override readonly model: AnthropicModelAlias,
        systemPrompt: string,
        rawTools: RawToolDefinition[],
        outputSchema: z.ZodType,
    ) {
        super(runLogDir, model, systemPrompt, rawTools, outputSchema);
    }

    private buildMessagesWithCache = (): ModelMessage[] => {
        const mapContent = <T extends TextPart | ToolResultPart>(content: T[], isLastMessage: boolean): T[] => {
            return content.map((c, contentIndex) => {
                const isLastContent = contentIndex === content.length - 1;
                if (!(isLastContent && isLastMessage)) return {...c};
                return {
                    ...c,
                    providerOptions: {
                        anthropic: {
                            cacheControl: {type: 'ephemeral'},
                        },
                    }
                }
            })
        }
        const mappedUserMessages = this.userMessages.map((message, index) => {
            const isLastMessage = index === this.userMessages.length - 1;
            if (message.role === 'user') {
                const mappedMessage: UserModelMessage = {
                    role: message.role,
                    content: mapContent<TextPart>(message.content as TextPart[], isLastMessage),
                };
                return {index: message.index, message: mappedMessage};
            }
            if (message.role === 'tool') {
                const mappedMessage: ToolModelMessage = {
                    role: message.role,
                    content: mapContent<ToolResultPart>(message.content as ToolResultPart[], isLastMessage),
                };
                return {index: message.index, message: mappedMessage};
            }
            throw new Error(`Unknown user message ${message}`)
        });
        const allMessages = [
            ...mappedUserMessages,
            ...this.assistantMessages
        ]
        return _.chain(allMessages)
            .orderBy(({index}) => index)
            .map(({message}) => message)
            .value()
    }

    public async callModelApi(): Promise<ModelResponse | undefined> {
        const timestamp = new Date();
        let result: Awaited<ReturnType<typeof generateText>> | undefined;
        let cacheUsage: CacheUsage | undefined;
        let httpResponse: Response | undefined;
        let url: RequestInfo | URL | undefined;
        let options: RequestInit | undefined;
        try {
            const anthropic = createAnthropic({
                apiKey: process.env.ANTHROPIC_API_KEY,
                fetch: async (requestUrl, requestOptions) => {
                    url = requestUrl;
                    options = requestOptions;
                    const res = await fetch(requestUrl, requestOptions);
                    httpResponse = res.clone();
                    return res;
                }
            })
            result = await generateText({
                model: anthropic(this.model),
                system: {
                    role: 'system',
                    content: this.systemPrompt,
                    providerOptions: {
                        anthropic: {
                            cacheControl: {type: 'ephemeral'},
                        }
                    }
                },
                messages: this.buildMessagesWithCache(),
                tools: this.sdkTools,
                // Claude 5 models think by default and thinking counts against
                // this cap, so leave headroom beyond the visible response.
                // Covers hidden reasoning tokens as well as the visible response on a
                // reasoning model, and reasoning is spent first. Raise it per turn with
                // setMaxOutputTokens when a lot of text has to come back.
                maxOutputTokens: this.maxOutputTokens,
                output: Output.object({schema: this.outputSchema}),
                providerOptions: {
                    anthropic: {
                        cacheControl: {type: "ephemeral"},
                    },
                }
            });

            cacheUsage = this.trackUsage(result.usage);
            const response = this.convertToModelResponse(result);
            this.logResponsePreview(result.text, result.reasoningText);
            this.storeAssistantMessages(result);
            return response;
        } catch (error) {
            const errorMessage = inspect(error)
            console.error(`Error when calling Anthropic API: ${errorMessage}`);
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

export const anthropicModelClientFactory = (
    runLogDir: string,
    model: AnthropicModelAlias,
    systemPrompt: string,
    rawTools: RawToolDefinition[],
    outputSchema: z.ZodType,
): ModelClient => {
    return new AnthropicModelClient(
        runLogDir,
        model,
        systemPrompt,
        rawTools,
        outputSchema,
    );
};