import {
    ModelMessage,
    Tool,
    LanguageModel, TypedToolCall, ToolSet, ToolModelMessage, UserModelMessage, ToolContent,
} from "ai";
import type {LanguageModelUsage, FinishReason} from "ai";
import type {z} from "zod";
import {SessionTokenUsage, ToolCall} from "@lib/model_clients/model_client.types";
import {AssistantModelMessage, TextPart, ToolResultPart} from "@ai-sdk/provider-utils";

export type {
    ModelMessage,
    Tool,
    LanguageModel,
    LanguageModelUsage,
    FinishReason,
};

export type ModelAlias =
    | "claude-opus-5"
    | "claude-sonnet-5"
    | "claude-haiku-4-5"
    | "gpt-5.6-sol"
    | "gpt-5.6-terra"
    | "gpt-5.6-luna"
    | "gemini-2.0-flash"
    | "gemini-2.5-flash"
    | "gemini-2.5-pro"
    | "deepseek-v4-pro"
    | "deepseek-v4-flash"
    | "kimi-k3"
    | "grok-4.5"
    | "grok-4.3";

export type ModelProvider = "anthropic" | "openai" | "google" | "deepseek" | "moonshot" | "xai";

export interface ToolCallResult {
    type: "tool_result";
    toolCallId: string;
    toolName: string,
    result: string;
    isError?: boolean;
}


export interface ModelResponse {
    text: string;
    finishReason: FinishReason;
    toolCalls: TypedToolCall<ToolSet>[];
    usage: LanguageModelUsage;
    responseMessages: ModelMessage[];
}

export interface ModelClientConfig {
    runLogDir: string;
    model: ModelAlias;
    systemPrompt: string;
    tools: Tool[];
    outputSchema?: z.ZodType;
}

export type UserMessage = {
    role: 'user',
    content: TextPart[],
    index: number;
}

export type UserToolMessage = {
    role: 'tool',
    content: ToolResultPart[];
    index: number;
}

export const toTextPart = (text: string) => {
    const textPart: TextPart = {
        type: "text",
        text
    }
    return textPart;
}

export const toToolResultPart = (result: ToolCallResult): ToolResultPart => {
    return {
        type: "tool-result",
        toolCallId: result.toolCallId,
        toolName: result.toolName,
        output: {type: "text", value: result.result}
    }
}

export interface ModelClient {
    readonly model: ModelAlias;

    /** The last API error message, if callModelApi failed (it otherwise swallows to undefined). */
    lastError?: string;

    addUserMessage(content: TextPart[]): void;

    addToolResultMessage(content: ToolResultPart[]): void;

    callModelApi(): Promise<ModelResponse | undefined>;

    setSystemPrompt(prompt: string): void;

    setOutputSchema(schema: z.ZodType): void;

    /** Estimated USD cost of every call this client has made so far. */
    getSessionCost(): number;

    /** Token counts accumulated over every call this client has made so far. */
    getSessionTokens(): SessionTokenUsage;

    /** Number of API calls made so far. */
    getApiCallCount(): number;
}

export type ModelClientFactory = (config: ModelClientConfig) => ModelClient;