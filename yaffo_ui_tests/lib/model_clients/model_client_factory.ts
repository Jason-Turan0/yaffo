import type {z} from "zod";
import {ModelAlias, ModelClient} from "@lib/model_clients/model_client.interface";
import {RawToolDefinition} from "@lib/tool_providers/toolprovider.types";
import {AnthropicModelAlias, anthropicModelClientFactory} from "@lib/model_clients/anthropic_model_client";
import {GeminiModelAlias, geminiModelClientFactory} from "@lib/model_clients/gemini_model_client";

const ANTHROPIC_MODELS: Set<string> = new Set<string>(["claude-opus-4-5", "claude-sonnet-4-5", "claude-haiku-4-5"]);
const GEMINI_MODELS: Set<string> = new Set<string>(["gemini-2.0-flash", "gemini-2.5-flash", "gemini-2.5-pro"]);

// The aliases createModelClient can actually construct — the single source of
// truth for CLI validation (a subset of the ModelAlias type, which also names
// providers that aren't wired up here).
export const KNOWN_MODEL_ALIASES: ModelAlias[] = [
    ...ANTHROPIC_MODELS,
    ...GEMINI_MODELS,
] as ModelAlias[];

export function isKnownModel(model: string): model is ModelAlias {
    return ANTHROPIC_MODELS.has(model) || GEMINI_MODELS.has(model);
}

export type WiredProvider = "anthropic" | "google";

export const PROVIDER_API_KEY_ENV: Record<WiredProvider, string> = {
    anthropic: "ANTHROPIC_API_KEY",
    google: "GOOGLE_GENERATIVE_AI_API_KEY",
};

export function providerForModel(model: string): WiredProvider | undefined {
    if (ANTHROPIC_MODELS.has(model)) return "anthropic";
    if (GEMINI_MODELS.has(model)) return "google";
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
    throw new Error(`Unsupported model: ${model}`);
}
