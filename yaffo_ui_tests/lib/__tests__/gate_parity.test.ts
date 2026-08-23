import {describe, expect, it} from "@jest/globals";
import {readFileSync} from "fs";
import {join} from "path";

const source = (name: string): string =>
    readFileSync(join(process.cwd(), "lib", "user_doc_automation", name), "utf8");

/**
 * Generation and healing produce the same two artifacts — a guide page and the
 * walkthrough that captures its screenshots — so they have to clear the same bar.
 *
 * They did not. Generation ran the walkthrough and checked what it produced; healing
 * only typechecked it. A heal that reframed a shot onto the wrong element passed every
 * gate it faced, and the next capture run promoted it. Nothing failed loudly, which is
 * why it survived — so the parity is asserted here rather than left to review.
 */
describe("generation and healing run the same gates", () => {
    it.each(["generate_cli.ts", "fix.ts"])("%s delegates to the shared gates", (file) => {
        expect(source(file)).toMatch(/\brunGates\(/);
    });

    it.each(["generate_cli.ts", "fix.ts"])("%s does not keep its own mkdocs gate", (file) => {
        // A second copy is how they drifted the first time.
        expect(source(file)).not.toMatch(/mkdocs["'\s,\]]*.*build.*--strict/s);
    });

    it("the shared gates actually execute the walkthrough", () => {
        // Typechecking proves the file compiles; only running it proves it reaches the
        // right view. This is the gate healing was missing.
        expect(source("gates.ts")).toMatch(/lib\/user_doc_automation\/run\.ts/);
    });

    // mkdocs --strict aborts on an image it cannot resolve, and it resolves against
    // docs/guide — not staging. So a page referencing a *new* screenshot cannot build
    // until that screenshot has been promoted. Capture must promote, and must run
    // before mkdocs. Getting this backwards passed only for pages whose images already
    // existed, which is why it survived review.
    it("promotes while capturing, so mkdocs can resolve new images", () => {
        const gates = source("gates.ts");
        const capture = gates.slice(gates.indexOf("export const capturesWhatThePageReferences"));
        expect(capture).toContain('"--promote"');
    });

    it("checks the promoted images in the guide, not in staging", () => {
        const gates = source("gates.ts");
        const capture = gates.slice(gates.indexOf("export const capturesWhatThePageReferences"),
                                    gates.indexOf("export const revertPage"));
        expect(capture).toContain("GUIDE_DIR");
        expect(capture).not.toContain("STAGING_DIR");
    });

    it("captures before mkdocs, since mkdocs depends on what it promoted", () => {
        const body = source("gates.ts");
        const runGates = body.slice(body.indexOf("export const runGates"));
        expect(runGates.indexOf("capturesWhatThePageReferences(")).toBeLessThan(
            runGates.indexOf("mkdocsStrict()"));
    });

    // Documenting "Open File" means clicking it, and the endpoint runs
    // `subprocess.run(["open", path])` against the real file — Preview windows on
    // whoever ran the capture, or processes on a CI runner.
    it("stubs the endpoints that reach outside the browser", () => {
        expect(source("runner.ts")).toMatch(/blockOsSideEffects\(context\)/);
        const routes = source("side_effects.ts");
        expect(routes).toContain("**/api/open-file");
        expect(routes).toContain("**/api/open-folder");
    });

    it("stubs them with success, so the shot shows the real state", () => {
        // Aborting would surface an error toast and document a failure.
        expect(source("side_effects.ts")).toMatch(/status:\s*200/);
    });

    it("reverts the lockfile and catalog, which are not in `written`", () => {
        const revert = source("gates.ts").slice(source("gates.ts").indexOf("export const revertPage"));
        expect(revert).toContain(".lock.json");
        expect(revert).toMatch(/\$\{name\}\.json/);
    });

    it("never reverts memories, which are meant to outlive a failed attempt", () => {
        const revert = source("gates.ts").slice(source("gates.ts").indexOf("export const revertPage"));
        expect(revert).not.toMatch(/paths.*memories|join\(pageDir, "memories"\)/);
    });

    // Promoting during verification means a rejected answer has left images behind.
    it.each(["generate_cli.ts", "fix.ts"])("%s takes the images back out on failure", (file) => {
        expect(source(file)).toMatch(/\brevertPage\(/);
    });

    it("runs the cheap gate before the expensive one", () => {
        const gates = source("gates.ts");
        const body = gates.slice(gates.indexOf("export const runGates"));
        expect(body.indexOf("typecheck()")).toBeLessThan(body.indexOf("capturesWhatThePageReferences("));
    });

    it("confines the walkthrough it executes to the env allowlist", () => {
        expect(source("gates.ts")).toMatch(/captureEnv\(process\.env/);
    });
});
