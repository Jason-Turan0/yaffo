import Anthropic from "@anthropic-ai/sdk";
import {writeFileSync} from "fs";
import {ApiLogEntry, CacheUsage} from "@lib/test_generator/model_client.types";
import {join} from "path";
import {BetaMessageParam, BetaTool} from "@anthropic-ai/sdk/resources/beta";

//Most expensive to least
export type AnthropicModelAliasOpus = 'claude-opus-4-5';
export type AnthropicModelAliasSonnet = 'claude-sonnet-4-5';
export type AnthropicModelAliasHaiku = 'claude-haiku-4-5';


export type AnthropicModelAlias = AnthropicModelAliasOpus | AnthropicModelAliasSonnet | AnthropicModelAliasHaiku;

export class AnthropicModelClient {
    private messages: BetaMessageParam[];
    private sessionInputTokens: number = 0;
    private sessionOutputTokens: number = 0;
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

    private buildToolsWithCache = (): BetaTool[] => {
        if (this.tools.length === 0) return [];
        return this.tools.map((tool, index) => {
            if (index === this.tools.length - 1) {
                return {...tool, cache_control: {type: "ephemeral" as const}};
            }
            return tool;
        });
    };

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
            system: this.buildSystemWithCache(),
            tools: this.buildToolsWithCache() as Anthropic.Messages.Tool[],
            betas: ['context-management-2025-06-27'],
            messages: this.messages,
            context_management: {},
        };

        try {
            response = await this.anthropic.beta.messages.create(params);
            if (response != null) {
                this.sessionInputTokens += response.usage.input_tokens;
                this.sessionOutputTokens += response.usage.output_tokens;
            }
            cacheUsage = this.extractCacheUsage(response);
            return response;
        } catch (error) {
            const errorMessage = typeof error === "string" ? error : (error as any)?.message || error?.toString() || '';
            console.error(`Error when calling Anthropic API ${errorMessage}`);
            return undefined;
        } finally {
            const durationMs = Date.now() - timestamp.getTime();

            this.writeApiLog({
                timestamp: timestamp.toISOString(),
                durationMs,
                request: params,
                response,
                success: response != null,
                cacheUsage,
            });
            this.apiCallCount += 1;
            console.log(`API Call Count: ${this.apiCallCount}. Input: ${this.sessionInputTokens} Output: ${this.sessionOutputTokens}`);
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