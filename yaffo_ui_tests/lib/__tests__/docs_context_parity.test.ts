import {describe, expect, it} from "@jest/globals";
import {readFileSync} from "fs";
import {join} from "path";

const runner = readFileSync(
    join(process.cwd(), "lib", "user_doc_automation", "runner.ts"), "utf8");
const config = readFileSync(join(process.cwd(), "playwright.config.ts"), "utf8");

/**
 * Walkthroughs are written in the idiom of the Playwright specs — the generator is
 * shown one as its reference — so the browser context they run in has to support that
 * idiom.
 *
 * It did not. `playwright.config.ts` sets `baseURL`; the docs runner did not, so
 * `page.waitForURL("/")` timed out after a navigation that had already succeeded:
 *
 *   page.waitForURL: Timeout 30000ms exceeded.
 *   waiting for navigation to "/" until "load"
 *     navigated to "http://host.docker.internal:5002/"
 *
 * A model cannot infer that, and under a containerized capture the address is
 * `host.docker.internal` anyway, so writing the absolute URL would not have helped.
 */
describe("the docs browser context matches the spec runner's", () => {
    it("sets baseURL, so relative URLs resolve as they do in specs", () => {
        expect(runner).toMatch(/baseURL:\s*baseUrl/);
    });

    it("is the same setting the spec config relies on", () => {
        expect(config).toMatch(/baseURL/);
    });

    it.each([
        ["deviceScaleFactor", /deviceScaleFactor:/],
        ["locale", /locale:\s*"en-US"/],
        ["timezone", /timezoneId:/],
        ["colorScheme", /colorScheme:/],
    ])("still pins %s, so a shot cannot change with the machine", (_l, pattern) => {
        expect(runner).toMatch(pattern);
    });
});
