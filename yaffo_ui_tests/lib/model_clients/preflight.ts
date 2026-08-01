/**
 * Cheap preflight for a model alias: verify the provider's API key is present
 * and that a minimal request actually succeeds — so a heal fails in ~1s (and a
 * fraction of a cent) instead of after spending on the full triage phase when
 * the key is missing/invalid or the account is out of credits.
 */
import {generateText} from "ai";
import type {LanguageModel} from "ai";
import {createAnthropic} from "@ai-sdk/anthropic";
import {createGoogleGenerativeAI} from "@ai-sdk/google";

import {ModelAlias} from "@lib/model_clients/model_client.interface";
import {PROVIDER_API_KEY_ENV, providerForModel, WiredProvider} from "@lib/model_clients/model_client_factory";
import {createSdkProviderModel} from "@lib/model_clients/sdk_provider_model_client";

export class PreflightError extends Error {}

function buildModel(provider: WiredProvider, model: ModelAlias): LanguageModel {
    if (provider === "anthropic") {
        return createAnthropic({apiKey: process.env.ANTHROPIC_API_KEY})(model);
    }
    if (provider === "google") {
        return createGoogleGenerativeAI({apiKey: process.env.GOOGLE_GENERATIVE_AI_API_KEY})(model);
    }
    return createSdkProviderModel(provider, model);
}

export async function preflightModel(model: ModelAlias): Promise<void> {
    const provider = providerForModel(model);
    if (!provider) {
        throw new PreflightError(`No wired provider for model alias: ${model}`);
    }

    const keyVar = PROVIDER_API_KEY_ENV[provider];
    if (!process.env[keyVar]) {
        throw new PreflightError(
            `${keyVar} is not set — required for model ${model} (provider ${provider}).`,
        );
    }

    try {
        // 16 rather than 1: reasoning models refuse very small output caps.
        await generateText({model: buildModel(provider, model), prompt: "ping", maxOutputTokens: 16});
    } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        throw new PreflightError(`Preflight API check failed for ${model}: ${message}`);
    }
}