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

export interface CacheUsage {
    cacheCreationInputTokens: number;
    cacheReadInputTokens: number;
    inputTokens: number;
    outputTokens: number;
    sessionCacheCreationInputTokens: number;
    sessionCacheReadInputTokens: number;
    sessionInputTokens: number;
    sessionOutputTokens: number;
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
    "claude-opus-4-5": {
        inputPerMillion: 5.00,
        outputPerMillion: 25.00,
        cacheWritePerMillion: 6.25,
        cacheReadPerMillion: 0.50,
    },
    "claude-sonnet-4-5": {
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