/**
 * Vision capability is a guard, not documentation.
 *
 * The failure it prevents is specific: DeepSeek's general models accept a request
 * carrying images, silently discard them, and answer from the surrounding text
 * sounding exactly as confident. Docs triage would then classify a screenshot it
 * never saw.
 */
import {
    MODEL_VISION_SUPPORT,
    VISION_MODEL_SUBSTITUTE,
    supportsVision,
    visionModelFor,
} from "@lib/model_clients/model_client.interface";
import type {ModelAlias} from "@lib/model_clients/model_client.interface";

// Deliberately not importing model_client_factory: it pulls the whole @ai-sdk
// provider chain, which this suite has no need to load.
const ALIASES = Object.keys(MODEL_VISION_SUPPORT) as ModelAlias[];

describe("MODEL_VISION_SUPPORT", () => {
    it("has an entry for every alias", () => {
        // Coverage is really enforced at compile time: the map is typed
        // Record<ModelAlias, boolean>, so omitting an alias fails tsc. This guards
        // the weaker property that the map is not empty.
        expect(ALIASES.length).toBeGreaterThan(0);
        for (const model of ALIASES) {
            expect(typeof MODEL_VISION_SUPPORT[model]).toBe("boolean");
        }
    });

    it("marks DeepSeek's general models as blind and its vision model as sighted", () => {
        expect(MODEL_VISION_SUPPORT["deepseek-v4-pro"]).toBe(false);
        expect(MODEL_VISION_SUPPORT["deepseek-v4-flash"]).toBe(false);
        expect(MODEL_VISION_SUPPORT["deepseek-v4-flash-vision-exp"]).toBe(true);
    });
});

describe("supportsVision", () => {
    it("is true for a model that receives images", () => {
        // Guards against the lookup being inverted, which would refuse every capable
        // model and admit exactly the ones that cannot see.
        expect(supportsVision("claude-sonnet-5")).toBe(true);
        expect(supportsVision("gemini-3.6-flash")).toBe(true);
    });

    it("is false for a model that drops them", () => {
        expect(supportsVision("deepseek-v4-pro")).toBe(false);
    });
});

describe("visionModelFor", () => {
    it("leaves a capable model alone", () => {
        expect(visionModelFor("claude-sonnet-5")).toBe("claude-sonnet-5");
    });

    it("swaps a blind DeepSeek model for the vision one", () => {
        expect(visionModelFor("deepseek-v4-pro")).toBe("deepseek-v4-flash-vision-exp");
        expect(visionModelFor("deepseek-v4-flash")).toBe("deepseek-v4-flash-vision-exp");
    });

    it("always resolves to something that can see", () => {
        for (const model of ALIASES) {
            expect(supportsVision(visionModelFor(model))).toBe(true);
        }
    });

    it("only substitutes where the provider splits vision out", () => {
        // Every other provider handles images on its ordinary models, so a growing
        // substitution table would mean something is being worked around.
        expect(Object.keys(VISION_MODEL_SUBSTITUTE).every((m) => m.startsWith("deepseek"))).toBe(true);
    });
});
