import type {MessageParam, ContentBlock, ToolResultBlockParam} from "@anthropic-ai/sdk/resources/messages.js";
import {AnthropicModelAlias} from "@lib/test_generator/anthropic_model_client";

export interface ToolCall {
    id: string;
    name: string;
    input: Record<string, unknown>;
}

export interface ConversationTurn {
    role: "user" | "assistant";
    content: string | ContentBlock[];
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


interface ModelPricing {
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

// Pricing source: https://platform.claude.com/docs/en/about-claude/pricing
// Cache write: 1.25x base input price, Cache read: 0.1x base input price
export const MODEL_PRICING: Record<AnthropicModelAlias, ModelPricing> = {
    'claude-opus-4-5': {
        inputPerMillion: 5.00,
        outputPerMillion: 25.00,
        cacheWritePerMillion: 6.25,
        cacheReadPerMillion: 0.50,
    },
    'claude-sonnet-4-5': {
        inputPerMillion: 3.00,
        outputPerMillion: 15.00,
        cacheWritePerMillion: 3.75,
        cacheReadPerMillion: 0.30,
    },
    'claude-haiku-4-5': {
        inputPerMillion: 1.00,
        outputPerMillion: 5.00,
        cacheWritePerMillion: 1.25,
        cacheReadPerMillion: 0.10,
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