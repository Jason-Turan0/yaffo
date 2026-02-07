import {
    ModelMessage,
    Tool,
    LanguageModel, TypedToolCall, ToolSet, ToolModelMessage, UserModelMessage, ToolContent,
} from "ai";
import type {LanguageModelUsage, FinishReason} from "ai";
import type {z} from "zod";
import {ToolCall} from "@lib/test_generator/model_client.types";
import {AssistantModelMessage, TextPart, ToolResultPart} from "@ai-sdk/provider-utils";

export type {
    ModelMessage,
    Tool,
    LanguageModel,
    LanguageModelUsage,
    FinishReason,
};

export type ModelAlias =
    | "claude-opus-4-5"
    | "claude-sonnet-4-5"
    | "claude-haiku-4-5"
    | "gpt-4o"
    | "gpt-4o-mini"
    | "gpt-4-turbo"
    | "gemini-2.0-flash"
    | "gemini-1.5-pro"
    | "deepseek-chat"
    | "deepseek-reasoner";

export type ModelProvider = "anthropic" | "openai" | "google" | "deepseek";

export interface ToolCallResult {
    type: "tool_result";
    toolCallId: string;
    toolName: string,
    result: string;
    isError?: boolean;
}


export interface ModelResponse<T = unknown> {
    text: string;
    finishReason: FinishReason;
    toolCalls: TypedToolCall<ToolSet>[];
    usage: LanguageModelUsage;
    responseMessages: ModelMessage[];
    output?: T;
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

    addUserMessage(content: TextPart[]): void;

    addToolResultMessage(content: ToolResultPart[]): void;

    callModelApi(): Promise<ModelResponse | undefined>;
}

export type ModelClientFactory = (config: ModelClientConfig) => ModelClient;