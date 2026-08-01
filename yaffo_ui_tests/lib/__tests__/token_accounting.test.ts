import {describe, expect, it} from "@jest/globals";
import {z} from "zod";
import {BaseModelClient} from "@lib/model_clients/base_model_client";
import {LanguageModelUsage, ModelAlias, ModelResponse} from "@lib/model_clients/model_client.interface";
import {CacheUsage} from "@lib/model_clients/model_client.types";

/**
 * The AI SDK reports `inputTokens` as the WHOLE prompt, with the cache buckets
 * as subsets of it — DeepSeek's `prompt_tokens` includes cache hits, and the
 * Anthropic provider sums input + cache_read + cache_creation itself. Counting
 * `inputTokens` and the cache buckets as separate additive figures double-bills
 * every cached token, which is what these tests pin down.
 */
class TestClient extends BaseModelClient {
    readonly logPrefix = "test";

    constructor(model: ModelAlias) {
        super("/tmp/does-not-matter", model, "", [], z.object({}));
    }

    callModelApi(): Promise<ModelResponse | undefined> {
        throw new Error("not used");
    }

    track(usage: LanguageModelUsage): CacheUsage {
        return this.trackUsage(usage);
    }
}

/** Usage exactly as @ai-sdk/openai-compatible builds it for DeepSeek. */
const deepseekUsage = (promptTokens: number, cachedTokens: number, completionTokens: number): LanguageModelUsage => ({
    inputTokens: promptTokens,
    inputTokenDetails: {
        noCacheTokens: promptTokens - cachedTokens,
        cacheReadTokens: cachedTokens,
        cacheWriteTokens: undefined,
    },
    outputTokens: completionTokens,
    outputTokenDetails: {textTokens: completionTokens, reasoningTokens: 0},
    totalTokens: promptTokens + completionTokens,
});

/** Usage exactly as @ai-sdk/anthropic builds it. */
const anthropicUsage = (
    freshInput: number, cacheRead: number, cacheWrite: number, output: number,
): LanguageModelUsage => ({
    inputTokens: freshInput + cacheRead + cacheWrite,
    inputTokenDetails: {
        noCacheTokens: freshInput,
        cacheReadTokens: cacheRead,
        cacheWriteTokens: cacheWrite,
    },
    outputTokens: output,
    outputTokenDetails: {textTokens: output, reasoningTokens: 0},
    totalTokens: freshInput + cacheRead + cacheWrite + output,
});

describe("trackUsage", () => {
    it("does not count DeepSeek cache hits as fresh input", () => {
        const client = new TestClient("deepseek-v4-pro");
        // prompt_tokens=10000 of which 9000 were cache hits.
        const usage = client.track(deepseekUsage(10_000, 9_000, 500));

        expect(usage.inputTokens).toBe(1_000);       // the miss portion only
        expect(usage.cacheReadInputTokens).toBe(9_000);
        expect(usage.totalInputTokens).toBe(10_000); // matches prompt_tokens
        // The buckets reconstruct the provider's own prompt_tokens exactly.
        expect(usage.inputTokens + usage.cacheReadInputTokens + usage.cacheCreationInputTokens)
            .toBe(usage.totalInputTokens);
    });

    it("keeps the session input total equal to the sum of prompt_tokens", () => {
        const client = new TestClient("deepseek-v4-pro");
        client.track(deepseekUsage(10_000, 9_000, 500));
        client.track(deepseekUsage(12_000, 11_500, 300));

        const tokens = client.getSessionTokens();
        expect(tokens.inputTokens).toBe(1_000 + 500);       // misses only
        expect(tokens.cacheReadTokens).toBe(9_000 + 11_500);
        expect(tokens.outputTokens).toBe(800);
        // Reported total equals what the provider billed: 22,000 in + 800 out.
        expect(tokens.totalTokens).toBe(22_000 + 800);
    });

    it("splits Anthropic's three input buckets without overlap", () => {
        const client = new TestClient("claude-sonnet-5");
        const usage = client.track(anthropicUsage(1_200, 30_000, 4_000, 700));

        expect(usage.inputTokens).toBe(1_200);
        expect(usage.cacheReadInputTokens).toBe(30_000);
        expect(usage.cacheCreationInputTokens).toBe(4_000);
        expect(usage.totalInputTokens).toBe(35_200);
        expect(client.getSessionTokens().totalTokens).toBe(35_200 + 700);
    });

    it("derives the uncached bucket when a provider omits noCacheTokens", () => {
        const client = new TestClient("deepseek-v4-pro");
        const usage = client.track({
            ...deepseekUsage(10_000, 9_000, 500),
            inputTokenDetails: {noCacheTokens: undefined, cacheReadTokens: 9_000, cacheWriteTokens: undefined},
        });
        expect(usage.inputTokens).toBe(1_000);
        expect(usage.totalInputTokens).toBe(10_000);
    });

    it("bills cached tokens at the cache rate only, never also at the input rate", () => {
        const client = new TestClient("deepseek-v4-pro");
        client.track(deepseekUsage(10_000, 9_000, 500));

        // 1k miss @ $0.435/M + 9k hit @ $0.003625/M + 500 out @ $0.87/M
        const expected = 1_000 / 1e6 * 0.435 + 9_000 / 1e6 * 0.003625 + 500 / 1e6 * 0.87;
        expect(client.getSessionCost()).toBeCloseTo(expected, 10);

        // The bug charged all 10k prompt tokens at the input rate on top of the
        // cache-read charge; that is materially more expensive.
        const doubleCounted = 10_000 / 1e6 * 0.435 + 9_000 / 1e6 * 0.003625 + 500 / 1e6 * 0.87;
        expect(client.getSessionCost()).toBeLessThan(doubleCounted);
    });
});
