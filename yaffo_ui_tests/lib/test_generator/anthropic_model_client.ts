import Anthropic from "@anthropic-ai/sdk";
import {writeFileSync} from "fs";
import {ApiLogEntry, CacheUsage, CostEstimate, MODEL_PRICING} from "@lib/test_generator/model_client.types";
import {join} from "path";
import {BetaMessageParam, BetaTextBlockParam, BetaTool} from "@anthropic-ai/sdk/resources/beta";

//Most expensive to least
export type AnthropicModelAliasOpus = 'claude-opus-4-5';
export type AnthropicModelAliasSonnet = 'claude-sonnet-4-5';
export type AnthropicModelAliasHaiku = 'claude-haiku-4-5';

export type AnthropicModelAlias = AnthropicModelAliasOpus | AnthropicModelAliasSonnet | AnthropicModelAliasHaiku;

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

export class AnthropicModelClient {
    private messages: BetaMessageParam[];
    private sessionInputTokens: number = 0;
    private sessionOutputTokens: number = 0;
    private sessionCacheReadInputTokens: number = 0;
    private sessionCacheCreationInputTokens: number = 0;
    private anthropic: Anthropic;
    private apiCallCount: number;

    constructor(
        private runLogDir: string,
        private model: AnthropicModelAlias,
        private systemPrompt: string,
        private tools: BetaTool[],
        anthropicFactory: () => Anthropic,
    ) {
        this.runLogDir = runLogDir;
        this.model = model;
        this.messages = [];
        this.anthropic = anthropicFactory();
        this.apiCallCount = 0;
    }

    public addMessage = (message: BetaMessageParam) => {
        this.messages.push(message);
    }

    public addMessages = (message: BetaMessageParam[]) => {
        this.messages.push(...message);
    }

    private buildSystemWithCache = (): Anthropic.Messages.TextBlockParam[] => {
        return [{
            type: "text" as const,
            text: this.systemPrompt,
            cache_control: {type: "ephemeral" as const},
        }];
    };

    private buildMessagesWithCache = (): BetaMessageParam[] => {
        return this.messages.map((message, index) => {
            const content = message.content as BetaTextBlockParam[];
            const messageContent = {
                role: message.role,
                content: content.map(c => ({...c}))
            }
            const isLastMessage = index === this.messages.length - 1
            if (isLastMessage) {
                const contentToSet = messageContent.content[messageContent.content.length - 1];
                if (contentToSet) {
                    contentToSet.cache_control = {type: "ephemeral" as const};
                }
            }
            return messageContent;
        })
    }

    private extractCacheUsage = (response: Anthropic.Beta.BetaMessage | undefined): CacheUsage | undefined => {
        if (!response?.usage) return undefined;
        const usage = response.usage as Anthropic.Messages.Usage & {
            cache_creation_input_tokens?: number;
            cache_read_input_tokens?: number;
        };
        return {
            cacheCreationInputTokens: usage.cache_creation_input_tokens ?? 0,
            cacheReadInputTokens: usage.cache_read_input_tokens ?? 0,
            inputTokens: usage.input_tokens,
            outputTokens: usage.output_tokens,
            sessionCacheCreationInputTokens: this.sessionCacheCreationInputTokens,
            sessionCacheReadInputTokens: this.sessionCacheReadInputTokens,
            sessionInputTokens: this.sessionInputTokens,
            sessionOutputTokens: this.sessionOutputTokens,
        };
    };

    public callModelApi = async (): Promise<Anthropic.Beta.BetaMessage | undefined> => {
        let response: Anthropic.Beta.BetaMessage | undefined;
        let cacheUsage: CacheUsage | undefined;
        const timestamp = new Date();
        const params = {
            model: this.model,
            max_tokens: 8192,
            tools: this.tools,
            betas: ['context-management-2025-06-27'],
            system: this.buildSystemWithCache(),
            messages: this.buildMessagesWithCache(),
            context_management: {},
        };

        try {
            response = await this.anthropic.beta.messages.create(params);
            if (response != null) {
                this.sessionInputTokens += response.usage.input_tokens;
                this.sessionOutputTokens += response.usage.output_tokens;
                this.sessionCacheCreationInputTokens += response.usage.cache_creation_input_tokens ?? 0;
                this.sessionCacheReadInputTokens += response.usage.cache_read_input_tokens ?? 0;
            }
            cacheUsage = this.extractCacheUsage(response);
            return response;
        } catch (error) {
            const errorMessage = typeof error === "string" ? error : (error as any)?.message || error?.toString() || '';
            console.error(`Error when calling Anthropic API ${errorMessage}`);
            return undefined;
        } finally {
            const durationMs = Date.now() - timestamp.getTime();
            const costEstimate = cacheUsage ? estimateCost(this.model, cacheUsage) : undefined;

            this.writeApiLog({
                timestamp: timestamp.toISOString(),
                durationMs,
                request: params,
                response,
                success: response != null,
                cacheUsage,
                costEstimate
            });
            this.apiCallCount += 1;
            //console.log(`API Call Count: ${this.apiCallCount}.`);
            if (response?.content && response.content.length > 0) {
                for (const contentElement of response.content) {
                    if ("text" in contentElement && contentElement.text != null && contentElement.text != '') {
                        console.log(`   🤖 ${contentElement?.text.slice(0, 200)}`);
                    }
                }
            }
        }
    };

    private writeApiLog = (entry: ApiLogEntry): void => {
        const logPath = join(this.runLogDir, `${this.apiCallCount}_claude_api.json`);
        writeFileSync(logPath, JSON.stringify(entry, null, 2));
    };


}

export const anthropicModelClientFactory = (
    runLogDir: string,
    model: AnthropicModelAlias,
    systemPrompt: string,
    tools: BetaTool[],
) => {
    return new AnthropicModelClient(
        runLogDir,
        model,
        systemPrompt,
        tools,
        () => new Anthropic(),
    );
};