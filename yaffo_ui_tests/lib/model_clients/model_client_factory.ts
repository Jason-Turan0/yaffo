import type {z} from "zod";
import {ModelAlias, ModelClient} from "@lib/model_clients/model_client.interface";
import {RawToolDefinition} from "@lib/tool_providers/toolprovider.types";
import {AnthropicModelAlias, anthropicModelClientFactory} from "@lib/model_clients/anthropic_model_client";
import {GeminiModelAlias, geminiModelClientFactory} from "@lib/model_clients/gemini_model_client";

const ANTHROPIC_MODELS: Set<string> = new Set<string>(["claude-opus-4-5", "claude-sonnet-4-5", "claude-haiku-4-5"]);
const GEMINI_MODELS: Set<string> = new Set<string>(["gemini-2.0-flash", "gemini-2.5-flash", "gemini-2.5-pro"]);

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
