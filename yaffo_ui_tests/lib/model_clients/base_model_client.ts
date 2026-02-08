import {
    generateText, jsonSchema, Tool, ToolResultPart, ToolModelMessage, UserModelMessage,
} from "ai";
import type {LanguageModelUsage} from "ai";
import {writeFileSync} from "fs";
import {join} from "path";
import type {z} from "zod";
import type {JSONSchema7, TextPart} from "ai";
import {
    ModelClient,
    ModelResponse,
    ModelMessage, UserMessage, UserToolMessage, ModelAlias,
} from "@lib/model_clients/model_client.interface";
import {RawToolDefinition} from "@lib/test_generator/toolprovider.types";
import {ApiLogEntry, CacheUsage, CostEstimate, MODEL_PRICING} from "@lib/model_clients/model_client.types";
import _ from 'lodash';

export function convertRawToolsToSdkTools(rawTools: RawToolDefinition[]): Record<string, Tool> {
    const tools: Record<string, Tool> = {};
    for (const rawTool of rawTools) {
        tools[rawTool.name] = {
            description: rawTool.description,
            inputSchema: jsonSchema(rawTool.inputSchema as JSONSchema7),
        };
    }
    return tools;
}

export abstract class BaseModelClient implements ModelClient {
    protected userMessages: (UserMessage | UserToolMessage)[] = [];
    protected assistantMessages: { index: number; message: ModelMessage }[] = [];
    protected sessionInputTokens: number = 0;
    protected sessionOutputTokens: number = 0;
    protected sessionCacheReadInputTokens: number = 0;
    protected sessionCacheCreationInputTokens: number = 0;
    protected apiCallCount: number = 0;
    protected sdkTools: Record<string, Tool>;

    abstract readonly logPrefix: string;

    constructor(
        protected runLogDir: string,
        public readonly model: ModelAlias,
        protected systemPrompt: string,
        rawTools: RawToolDefinition[],
        protected outputSchema: z.ZodType,
    ) {
        this.sdkTools = convertRawToolsToSdkTools(rawTools);
    }

    addUserMessage(content: TextPart[]): void {
        this.userMessages.push({role: 'user', content: content, index: this.getNextIndex()});
    }

    addToolResultMessage(content: ToolResultPart[]): void {
        this.userMessages.push({role: 'tool', content: content, index: this.getNextIndex()});
    }

    abstract callModelApi(): Promise<ModelResponse | undefined>;

    protected getNextIndex = (): number => {
        if (this.userMessages.length === 0 && this.assistantMessages.length === 0) {
            return 0;
        }
        return (_.max([
            ...this.userMessages.map(m => m.index),
            ...this.assistantMessages.map(m => m.index),
        ]) as number) + 1;
    }

    protected buildOrderedMessages = (): ModelMessage[] => {
        const mappedUserMessages = this.userMessages.map((message) => {
            if (message.role === 'user') {
                const mappedMessage: UserModelMessage = {
                    role: message.role,
                    content: [...message.content] as TextPart[],
                };
                return {index: message.index, message: mappedMessage};
            }
            if (message.role === 'tool') {
                const mappedMessage: ToolModelMessage = {
                    role: message.role,
                    content: [...message.content] as ToolResultPart[],
                };
                return {index: message.index, message: mappedMessage};
            }
            throw new Error(`Unknown user message ${message}`);
        });
        const allMessages = [
            ...mappedUserMessages,
            ...this.assistantMessages
        ];
        return _.chain(allMessages)
            .orderBy(({index}) => index)
            .map(({message}) => message)
            .value();
    }

    protected trackUsage(usage: LanguageModelUsage): CacheUsage {
        const inputTokens = usage.inputTokens ?? 0;
        const outputTokens = usage.outputTokens ?? 0;
        const cacheReadTokens = usage.inputTokenDetails?.cacheReadTokens ?? 0;
        const cacheWriteTokens = usage.inputTokenDetails?.cacheWriteTokens ?? 0;

        this.sessionInputTokens += inputTokens;
        this.sessionOutputTokens += outputTokens;
        this.sessionCacheReadInputTokens += cacheReadTokens;
        this.sessionCacheCreationInputTokens += cacheWriteTokens;

        return {
            inputTokens,
            outputTokens,
            cacheCreationInputTokens: cacheWriteTokens,
            cacheReadInputTokens: cacheReadTokens,
            sessionInputTokens: this.sessionInputTokens,
            sessionOutputTokens: this.sessionOutputTokens,
            sessionCacheCreationInputTokens: this.sessionCacheCreationInputTokens,
            sessionCacheReadInputTokens: this.sessionCacheReadInputTokens,
        };
    }

    protected estimateCost(usage: CacheUsage): CostEstimate | undefined {
        const pricing = MODEL_PRICING[this.model];
        if (!pricing) return undefined;

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
    }

    protected convertToModelResponse(result: Awaited<ReturnType<typeof generateText>>): ModelResponse {
        let text: string | undefined = undefined;
        let output: unknown | undefined;
        try {
            text = result.text;
            console.log(result.text);
        } catch {
            text = "";
        }

        return {
            text: text,
            finishReason: result.finishReason,
            toolCalls: result.toolCalls,
            usage: result.usage,
            responseMessages: result.response.messages as ModelMessage[],

        };
    }

    protected storeAssistantMessages(result: Awaited<ReturnType<typeof generateText>>): void {
        if (result?.response?.messages?.length > 0) {
            for (const message of result.response.messages) {
                this.assistantMessages.push({index: this.getNextIndex(), message: message});
            }
        }
    }

    protected writeApiLog(entry: ApiLogEntry): void {
        const logPath = join(this.runLogDir, `${this.apiCallCount}_${this.logPrefix}_api.json`);
        writeFileSync(logPath, JSON.stringify(entry, null, 2));
    }
}