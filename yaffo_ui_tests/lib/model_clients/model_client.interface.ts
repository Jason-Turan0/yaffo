import {
    ModelMessage,
    Tool,
    LanguageModel, TypedToolCall, ToolSet, ToolModelMessage, UserModelMessage, ToolContent,
} from "ai";
import type {LanguageModelUsage, FinishReason} from "ai";
import type {z} from "zod";
import {SessionTokenUsage, ToolCall} from "@lib/model_clients/model_client.types";
import {AssistantModelMessage, FilePart, TextPart, ToolResultPart} from "@ai-sdk/provider-utils";

export type {
    ModelMessage,
    Tool,
    LanguageModel,
    LanguageModelUsage,
    FinishReason,
};

/**
 * Default per-call output budget.
 *
 * On a reasoning model this covers hidden reasoning tokens as well as the visible
 * answer, and reasoning is spent first — so a turn that must emit a lot of text needs
 * this raised, not just enough room for the text itself.
 */
export const DEFAULT_MAX_OUTPUT_TOKENS = 16000;

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
    | "gemini-3.6-flash"
    | "deepseek-v4-pro"
    | "deepseek-v4-flash"
    | "deepseek-v4-flash-vision-exp"
    | "kimi-k3"
    | "grok-4.5"
    | "grok-4.3";


/**
 * Every provider here handles images natively except DeepSeek's general-purpose
 * models, which need the dedicated vision model instead.
 *
 * DeepSeek is why this map exists: its ordinary models accept a request containing
 * images, silently discard them, and answer from the surrounding text — so a caller
 * that needs vision gets a confident, evidence-free answer with no error to notice.
 * See https://api-docs.deepseek.com/guides/vision/
 */
export const MODEL_VISION_SUPPORT: Record<ModelAlias, boolean> = {
    "claude-opus-5": true,
    "claude-sonnet-5": true,
    "claude-haiku-4-5": true,

    "gpt-5.6-sol": true,
    "gpt-5.6-terra": true,
    "gpt-5.6-luna": true,

    "gemini-2.0-flash": true,
    "gemini-2.5-flash": true,
    "gemini-2.5-pro": true,
    "gemini-3.6-flash": true,

    // DeepSeek's general models discard images rather than refusing them; only the
    // dedicated vision model actually receives them.
    "deepseek-v4-pro": false,
    "deepseek-v4-flash": false,
    "deepseek-v4-flash-vision-exp": true,

    "kimi-k3": true,
    "grok-4.5": true,
    "grok-4.3": true,
};

export const supportsVision = (model: ModelAlias): boolean =>
    MODEL_VISION_SUPPORT[model];

/**
 * The model to use instead when a caller needs images and the requested one cannot
 * receive them. Only DeepSeek splits vision into a separate model; every other
 * provider's models handle images directly.
 */
export const VISION_MODEL_SUBSTITUTE: Partial<Record<ModelAlias, ModelAlias>> = {
    "deepseek-v4-pro": "deepseek-v4-flash-vision-exp",
    "deepseek-v4-flash": "deepseek-v4-flash-vision-exp",
};

/**
 * Resolve a requested model to one that can actually see. Returns the model
 * unchanged when it already supports images, and the same model back when no
 * substitute exists — callers still have to check `supportsVision` on the result.
 */
export const visionModelFor = (model: ModelAlias): ModelAlias =>
    supportsVision(model) ? model : (VISION_MODEL_SUBSTITUTE[model] ?? model);

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
    /**
     * The model's hidden thinking, on providers that expose it.
     *
     * Diagnostic only — never a substitute for `text`. The two are separated by the
     * provider on purpose, and the one case where `text` is empty while this is full
     * is a truncated turn: promoting this to the answer would feed prose to a caller
     * expecting JSON and turn a clean failure into a plausible wrong result.
     */
    reasoningText?: string;
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

/**
 * What a user turn may carry. Images are needed by callers that ask the model to
 * judge something visual — the docs automation classifies a screenshot diff, which
 * it cannot do from text alone.
 */
export type UserContentPart = TextPart | FilePart;

export type UserMessage = {
    role: 'user',
    content: UserContentPart[],
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

/**
 * An image turn part. `data` is raw bytes; the SDK base64-encodes per provider.
 *
 * A `file` part rather than the older `image` part, which the AI SDK deprecated in
 * favour of `file` carrying an `image/*` media type.
 */
export const toImagePart = (data: Uint8Array, mediaType: string): FilePart => {
    const filePart: FilePart = {
        type: "file",
        data,
        mediaType,
    };
    return filePart;
};

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

    addUserMessage(content: UserContentPart[]): void;

    addToolResultMessage(content: ToolResultPart[]): void;

    callModelApi(): Promise<ModelResponse | undefined>;

    setSystemPrompt(prompt: string): void;

    setOutputSchema(schema: z.ZodType): void;
    /**
     * Raise the per-call output budget for a turn that has a lot to say.
     *
     * On a reasoning model this cap covers hidden reasoning *and* the visible answer,
     * and the reasoning is spent first. A turn asked to return two complete files can
     * burn the whole default on thinking and return an empty string — which surfaces
     * downstream as "response was not JSON", naming the wrong problem entirely.
     */
    setMaxOutputTokens(tokens: number): void;

    /** Estimated USD cost of every call this client has made so far. */
    getSessionCost(): number;

    /** Token counts accumulated over every call this client has made so far. */
    getSessionTokens(): SessionTokenUsage;

    /** Number of API calls made so far. */
    getApiCallCount(): number;
}

export type ModelClientFactory = (config: ModelClientConfig) => ModelClient;