import type {z} from "zod";
import {ModelAlias, ModelClient} from "@lib/model_clients/model_client.interface";
import {RawToolDefinition} from "@lib/tool_providers/toolprovider.types";
import {AnthropicModelAlias, anthropicModelClientFactory} from "@lib/model_clients/anthropic_model_client";
import {GeminiModelAlias, geminiModelClientFactory} from "@lib/model_clients/gemini_model_client";
import {
    SdkProvider,
    SdkProviderModelAlias,
    sdkProviderModelClientFactory,
} from "@lib/model_clients/sdk_provider_model_client";

const ANTHROPIC_MODELS: Set<string> = new Set<string>(["claude-opus-5", "claude-sonnet-5", "claude-haiku-4-5"]);
const GEMINI_MODELS: Set<string> = new Set<string>([
    "gemini-2.0-flash", "gemini-2.5-flash", "gemini-2.5-pro", "gemini-3.6-flash",
]);
const OPENAI_MODELS: Set<string> = new Set<string>(["gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna"]);
const DEEPSEEK_MODELS: Set<string> = new Set<string>([
    "deepseek-v4-pro", "deepseek-v4-flash", "deepseek-v4-flash-vision-exp",
]);
const MOONSHOT_MODELS: Set<string> = new Set<string>(["kimi-k3"]);
const XAI_MODELS: Set<string> = new Set<string>(["grok-4.5", "grok-4.3"]);

// The aliases createModelClient can actually construct — the single source of
// truth for CLI validation (a subset of the ModelAlias type, which also names
// providers that aren't wired up here).
export const KNOWN_MODEL_ALIASES: ModelAlias[] = [
    ...ANTHROPIC_MODELS,
    ...GEMINI_MODELS,
    ...OPENAI_MODELS,
    ...DEEPSEEK_MODELS,
    ...MOONSHOT_MODELS,
    ...XAI_MODELS,
] as ModelAlias[];

export function isKnownModel(model: string): model is ModelAlias {
    return KNOWN_MODEL_ALIASES.includes(model as ModelAlias);
}

export const DEFAULT_MODEL: ModelAlias = "claude-sonnet-5";

/**
 * Default model for generation and healing when no --model flag is given: the
 * MODEL_ALIAS environment variable (from the shell or .env) when set, else
 * DEFAULT_MODEL. Returned unvalidated so callers surface unknown aliases
 * through their usual isKnownModel error path.
 */
export function defaultModel(): string {
    return process.env.MODEL_ALIAS?.trim() || DEFAULT_MODEL;
}

export type WiredProvider = "anthropic" | "google" | SdkProvider;

export const PROVIDER_API_KEY_ENV: Record<WiredProvider, string> = {
    anthropic: "ANTHROPIC_API_KEY",
    google: "GOOGLE_GENERATIVE_AI_API_KEY",
    openai: "OPENAI_API_KEY",
    deepseek: "DEEPSEEK_API_KEY",
    moonshot: "MOONSHOT_API_KEY",
    xai: "XAI_API_KEY",
};

const SDK_PROVIDER_MODELS: Record<SdkProvider, Set<string>> = {
    openai: OPENAI_MODELS,
    deepseek: DEEPSEEK_MODELS,
    moonshot: MOONSHOT_MODELS,
    xai: XAI_MODELS,
};

export function providerForModel(model: string): WiredProvider | undefined {
    if (ANTHROPIC_MODELS.has(model)) return "anthropic";
    if (GEMINI_MODELS.has(model)) return "google";
    for (const [provider, models] of Object.entries(SDK_PROVIDER_MODELS)) {
        if (models.has(model)) return provider as SdkProvider;
    }
    return undefined;
}

export function supportsNativeStructuredOutput(model: ModelAlias): boolean {
    return ANTHROPIC_MODELS.has(model);
}

export function createModelClient(
    runLogDir: string,
    model: ModelAlias,
    systemPrompt: string,
    rawTools: RawToolDefinition[],
    outputSchema: z.ZodType,
): ModelClient {
    if (ANTHROPIC_MODELS.has(model)) {
        return anthropicModelClientFactory(runLogDir, model as AnthropicModelAlias, systemPrompt, rawTools, outputSchema);
    }
    if (GEMINI_MODELS.has(model)) {
        return geminiModelClientFactory(runLogDir, model as GeminiModelAlias, systemPrompt, rawTools, outputSchema);
    }
    const provider = providerForModel(model);
    if (provider && provider !== "anthropic" && provider !== "google") {
        return sdkProviderModelClientFactory(provider, runLogDir, model as SdkProviderModelAlias, systemPrompt, rawTools, outputSchema);
    }
    throw new Error(`Unsupported model: ${model}`);
}