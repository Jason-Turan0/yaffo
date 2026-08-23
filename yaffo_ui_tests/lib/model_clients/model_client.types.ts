import {ModelAlias} from "@lib/model_clients/model_client.interface";

export interface ToolCall {
    id: string;
    name: string;
    input: Record<string, unknown>;
}

export interface ConversationTurn {
    role: "user" | "assistant";
    content: string | Array<{type: string; text?: string}>;
    toolCalls?: ToolCall[];
    toolResults?: Array<{ toolUseId: string; result: unknown; truncated?: boolean }>;
}

/**
 * Token counts for one call plus the running session totals.
 *
 * The three input buckets are disjoint: the AI SDK reports `usage.inputTokens`
 * as the *total* prompt size with `cacheReadTokens`/`cacheWriteTokens` as
 * subsets of it (DeepSeek's `prompt_tokens` includes cache hits; the Anthropic
 * provider sums the three itself). `inputTokens` here is the uncached remainder
 * only — adding it to the two cache buckets reconstructs the total, which is
 * what `totalInputTokens` holds.
 */
export interface CacheUsage {
    cacheCreationInputTokens: number;
    cacheReadInputTokens: number;
    /** Uncached input tokens — excludes both cache buckets. */
    inputTokens: number;
    /** Whole prompt: uncached + cache read + cache write. */
    totalInputTokens: number;
    outputTokens: number;
    sessionCacheCreationInputTokens: number;
    sessionCacheReadInputTokens: number;
    /** Session uncached input tokens — excludes both cache buckets. */
    sessionInputTokens: number;
    sessionTotalInputTokens: number;
    sessionOutputTokens: number;
}

/** Token counts accumulated across every API call a client has made. */
export interface SessionTokenUsage {
    inputTokens: number;
    outputTokens: number;
    cacheWriteTokens: number;
    cacheReadTokens: number;
    /** Sum of the four counts above. */
    totalTokens: number;
}

export interface ModelPricing {
    inputPerMillion: number;
    outputPerMillion: number;
    cacheWritePerMillion: number;
    cacheReadPerMillion: number;
}

export interface CostEstimate {
    call: {
        inputCost: number;
        outputCost: number;
        cacheWriteCost: number;
        cacheReadCost: number;
        totalCost: number;
    };
    session: {
        inputCost: number;
        outputCost: number;
        cacheWriteCost: number;
        cacheReadCost: number;
        totalCost: number;
    };
}

export const MODEL_PRICING: Partial<Record<ModelAlias, ModelPricing>> = {
    "claude-opus-5": {
        inputPerMillion: 5.00,
        outputPerMillion: 25.00,
        cacheWritePerMillion: 6.25,
        cacheReadPerMillion: 0.50,
    },
    "claude-sonnet-5": {
        inputPerMillion: 3.00,
        outputPerMillion: 15.00,
        cacheWritePerMillion: 3.75,
        cacheReadPerMillion: 0.30,
    },
    "claude-haiku-4-5": {
        inputPerMillion: 1.00,
        outputPerMillion: 5.00,
        cacheWritePerMillion: 1.25,
        cacheReadPerMillion: 0.10,
    },
    "gemini-2.0-flash": {
        inputPerMillion: 0.10,
        outputPerMillion: 0.40,
        cacheWritePerMillion: 0,
        cacheReadPerMillion: 0,
    },
    "gemini-2.5-flash": {
        inputPerMillion: 0.30,
        outputPerMillion: 2.50,
        cacheWritePerMillion: 0,
        cacheReadPerMillion: 0.03,
    },
    "gemini-2.5-pro": {
        inputPerMillion: 1.25,
        outputPerMillion: 10.00,
        cacheWritePerMillion: 0,
        cacheReadPerMillion: 0.125,
    },
    "gemini-3.6-flash": {
        inputPerMillion: 0.30,
        outputPerMillion: 2.50,
        cacheWritePerMillion: 0,
        cacheReadPerMillion: 0.03,
    },
    // OpenAI, DeepSeek, Kimi, and Grok cache automatically with no write
    // premium, so cacheWritePerMillion is 0 (same convention as Gemini).
    "gpt-5.6-sol": {
        inputPerMillion: 5.00,
        outputPerMillion: 30.00,
        cacheWritePerMillion: 0,
        cacheReadPerMillion: 0.50,
    },
    "gpt-5.6-terra": {
        inputPerMillion: 2.00,
        outputPerMillion: 12.00,
        cacheWritePerMillion: 0,
        cacheReadPerMillion: 0.20,
    },
    "gpt-5.6-luna": {
        inputPerMillion: 0.20,
        outputPerMillion: 1.20,
        cacheWritePerMillion: 0,
        cacheReadPerMillion: 0.02,
    },
    "deepseek-v4-pro": {
        inputPerMillion: 0.435,
        outputPerMillion: 0.87,
        cacheWritePerMillion: 0,
        cacheReadPerMillion: 0.003625,
    },
    "deepseek-v4-flash": {
        inputPerMillion: 0.14,
        outputPerMillion: 0.28,
        cacheWritePerMillion: 0,
        cacheReadPerMillion: 0.0028,
    },
    // Assumed to match flash; the vision model's rates were not confirmed, so cost
    // reporting for it is an estimate.
    "deepseek-v4-flash-vision-exp": {
        inputPerMillion: 0.14,
        outputPerMillion: 0.28,
        cacheWritePerMillion: 0,
        cacheReadPerMillion: 0.0028,
    },
    "kimi-k3": {
        inputPerMillion: 3.00,
        outputPerMillion: 15.00,
        cacheWritePerMillion: 0,
        cacheReadPerMillion: 0.30,
    },
    "grok-4.5": {
        inputPerMillion: 2.00,
        outputPerMillion: 6.00,
        cacheWritePerMillion: 0,
        cacheReadPerMillion: 0.30,
    },
    "grok-4.3": {
        inputPerMillion: 1.25,
        outputPerMillion: 2.50,
        cacheWritePerMillion: 0,
        cacheReadPerMillion: 0.20,
    },
};

export interface ApiLogEntry {
    timestamp: string;
    request: unknown;
    response: unknown;
    durationMs?: number;
    success: boolean;
    cacheUsage?: CacheUsage;
    costEstimate?: CostEstimate;
}