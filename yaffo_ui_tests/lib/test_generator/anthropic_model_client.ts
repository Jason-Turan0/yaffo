import {
    generateText, jsonSchema, Tool, ToolResultPart, Output, UserModelMessage, ToolModelMessage,
    AssistantModelMessage, UserContent, AssistantContent
} from "ai";
import {anthropic, createAnthropic} from "@ai-sdk/anthropic";
import {writeFileSync} from "fs";
import {join} from "path";
import type {z} from "zod";
import type {JSONSchema7, TextPart} from "ai";
import {
    ModelClient,
    ModelResponse,
    ModelMessage, UserMessage, UserToolMessage,
} from "@lib/test_generator/model_client.interface";
import {RawToolDefinition} from "@lib/test_generator/toolprovider.types";
import {ApiLogEntry, CacheUsage, CostEstimate, MODEL_PRICING} from "@lib/test_generator/model_client.types";
import {inspect} from "node:util";
import _ from 'lodash';

export type AnthropicModelAlias = "claude-opus-4-5" | "claude-sonnet-4-5" | "claude-haiku-4-5";

export const estimateCost = (model: AnthropicModelAlias, usage: CacheUsage): CostEstimate => {
    const pricing = MODEL_PRICING[model];
    const toMillions = (tokens: number): number => tokens / 1_000_000;

    const callInputCost = toMillions(usage.inputTokens) * pricing.inputPerMillion;
    const callOutputCost = toMillions(usage.outputTokens) * pricing.outputPerMillion;
    const callCacheWriteCost = toMillions(usage.cacheCreationInputTokens) * pricing.cacheWritePerMillion;
    const callCacheReadCost = toMillions(usage.cacheReadInputTokens) * pricing.cacheReadPerMillion;

    const sessionInputCost = toMillions(usage.sessionInputTokens) * pricing.inputPerMillion;
    const sessionOutputCost = toMillions(usage.sessionOutputTokens) * pricing.outputPerMillion;
    const sessionCacheWriteCost = toMillions(usage.sessionCacheCreationInputTokens) * pricing.cacheWritePerMillion;
    const sessionCacheReadCost = toMillions(usage.sessionCacheReadInputTokens) * pricing.cacheReadPerMillion;

    return {
        call: {
            inputCost: callInputCost,
            outputCost: callOutputCost,
            cacheWriteCost: callCacheWriteCost,
            cacheReadCost: callCacheReadCost,
            totalCost: callInputCost + callOutputCost + callCacheWriteCost + callCacheReadCost,
        },
        session: {
            inputCost: sessionInputCost,
            outputCost: sessionOutputCost,
            cacheWriteCost: sessionCacheWriteCost,
            cacheReadCost: sessionCacheReadCost,
            totalCost: sessionInputCost + sessionOutputCost + sessionCacheWriteCost + sessionCacheReadCost,
        },
    };
};

function convertRawToolsToSdkTools(rawTools: RawToolDefinition[]): Record<string, Tool> {
    const tools: Record<string, Tool> = {};
    for (const rawTool of rawTools) {
        tools[rawTool.name] = {
            description: rawTool.description,
            inputSchema: jsonSchema(rawTool.inputSchema as JSONSchema7),
        };
    }
    return tools;
}

export class AnthropicModelClient implements ModelClient {
    private userMessages: (UserMessage | UserToolMessage)[] = [];
    private assistantMessages: { index: number; message: ModelMessage }[] = [];
    private sessionInputTokens: number = 0;
    private sessionOutputTokens: number = 0;
    private sessionCacheReadInputTokens: number = 0;
    private sessionCacheCreationInputTokens: number = 0;
    private apiCallCount: number = 0;
    private sdkTools: Record<string, Tool>;

    constructor(
        private runLogDir: string,
        public readonly model: AnthropicModelAlias,
        private systemPrompt: string,
        rawTools: RawToolDefinition[],
        private outputSchema: z.ZodType,
    ) {
        this.sdkTools = convertRawToolsToSdkTools(rawTools);
    }

    addUserMessage(content: TextPart[]): void {
        this.userMessages.push({role: 'user', content: content, index: this.getNextIndex()});
    }

    addToolResultMessage(content: ToolResultPart[]): void {
        this.userMessages.push({role: 'tool', content: content, index: this.getNextIndex()});
    }

    private getNextIndex = (): number => {
        if (this.userMessages.length === 0 && this.assistantMessages.length === 0) {
            return 0
        }
        return (_.max([
            ...this.userMessages.map(m => m.index),
            ...this.assistantMessages.map(m => m.index),
        ]) as number) + 1;
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
                model: anthropic('claude-sonnet-4-5'),
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
                maxOutputTokens: 8192,
                output: Output.object({schema: this.outputSchema}),
                providerOptions: {
                    anthropic: {
                        cacheControl: {type: "ephemeral"},
                    },
                }
            });

            const usage = result.usage;
            const inputTokens = usage.inputTokens ?? 0;
            const outputTokens = usage.outputTokens ?? 0;
            const cacheReadTokens = usage.inputTokenDetails?.cacheReadTokens ?? 0;
            const cacheWriteTokens = usage.inputTokenDetails?.cacheWriteTokens ?? 0;

            this.sessionInputTokens += inputTokens;
            this.sessionOutputTokens += outputTokens;
            this.sessionCacheReadInputTokens += cacheReadTokens;
            this.sessionCacheCreationInputTokens += cacheWriteTokens;

            cacheUsage = {
                inputTokens,
                outputTokens,
                cacheCreationInputTokens: cacheWriteTokens,
                cacheReadInputTokens: cacheReadTokens,
                sessionInputTokens: this.sessionInputTokens,
                sessionOutputTokens: this.sessionOutputTokens,
                sessionCacheCreationInputTokens: this.sessionCacheCreationInputTokens,
                sessionCacheReadInputTokens: this.sessionCacheReadInputTokens,
            };
            const response = this.convertToModelResponse(result);
            if (result.text) {
                console.log(`   🤖 ${result.text.slice(0, 200)}`);
            }
            if (result?.response?.messages?.length > 0) {
                for (const message of result?.response?.messages) {
                    this.assistantMessages.push({index: this.getNextIndex(), message: message});
                }
            }
            return response;
        } catch (error) {
            const errorMessage = inspect(error)
            console.error(`Error when calling Anthropic API: ${errorMessage}`);
            return undefined;
        } finally {
            const durationMs = Date.now() - timestamp.getTime();
            const costEstimate = cacheUsage ? estimateCost(this.model, cacheUsage) : undefined;
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

    private convertToModelResponse(result: Awaited<ReturnType<typeof generateText>>): ModelResponse {
        let text: string | undefined = undefined;
        let output: unknown | undefined;
        try {
            text = result.text;
            output = JSON.parse(text);
        } catch (e) {
            text = "";
        }

        return {
            text: text,
            finishReason: result.finishReason,
            toolCalls: result.toolCalls,
            usage: result.usage,
            responseMessages: result.response.messages as ModelMessage[],
            output: output,
        };
    }

    private writeApiLog(entry: ApiLogEntry): void {
        const logPath = join(this.runLogDir, `${this.apiCallCount}_claude_api.json`);
        writeFileSync(logPath, JSON.stringify(entry, null, 2));
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