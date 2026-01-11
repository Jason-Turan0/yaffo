import type {MessageParam, ContentBlock, ToolResultBlockParam} from "@anthropic-ai/sdk/resources/messages.js";

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
  sessionInputTokens: number;
  sessionOutputTokens: number;
}

export interface ApiLogEntry {
  timestamp: string;
  request: unknown;
  response: unknown;
  durationMs?: number;
  success: boolean;
  cacheUsage?: CacheUsage;

}