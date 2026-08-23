import {describe, expect, it} from "@jest/globals";
import {readFileSync} from "fs";
import {join} from "path";

const client = (name: string): string =>
    readFileSync(join(process.cwd(), "lib", "model_clients", name), "utf8");

const CLIENTS = ["sdk_provider_model_client.ts", "anthropic_model_client.ts",
                 "gemini_model_client.ts"];

/**
 * `setMaxOutputTokens` is only useful if every client reads the field.
 *
 * Gemini kept a hardcoded 8192 after the setter was added, so a docs generate turn
 * asking for 48000 silently got 8192 — and was measured at 7448 tokens, 91% of it. A
 * cap that is ignored fails as truncation in the *next* slightly longer answer, and
 * truncation surfaces as "response was not JSON", which points nowhere near the cause.
 */
describe.each(CLIENTS)("%s", (name) => {
    it("uses the configurable budget", () => {
        expect(client(name)).toMatch(/maxOutputTokens:\s*this\.maxOutputTokens/);
    });

    it("does not hardcode a number", () => {
        expect(client(name)).not.toMatch(/maxOutputTokens:\s*\d+/);
    });
});
