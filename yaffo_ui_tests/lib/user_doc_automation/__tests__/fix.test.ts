import {afterAll, afterEach, beforeEach, describe, expect, it, jest} from "@jest/globals";
import {existsSync, mkdirSync, readFileSync, rmSync, writeFileSync} from "fs";
import {dirname, join, resolve} from "path";
import type {Evidence} from "../evidence";
import type {Fix} from "../fix";

const toTextPart = jest.fn((text: string) => ({type: "text", text}));
const runToolLoop = jest.fn<(client: unknown, providers: unknown[]) => Promise<string>>();
const parseAnswer = jest.fn<(...args: unknown[]) => {value?: Fix; errors: string[]}>();
const runGates = jest.fn<(page: string, options?: {useDocker?: boolean}) => string[]>();
const revertPage = jest.fn<(page: string, written: string[]) => void>();

await jest.unstable_mockModule("@lib/model_clients/model_client.interface", () => ({toTextPart}));
await jest.unstable_mockModule("../tool_loop", () => ({parseAnswer, runToolLoop}));
await jest.unstable_mockModule("../gates", () => ({revertPage, runGates}));

const {applyFix, FixSchema, MAX_FIX_ATTEMPTS} = await import("../fix");

const REPO = resolve(join(process.cwd(), ".."));
const AREA = `__fix_unit_${process.pid}_${Date.now()}`;
const PAGE = `${AREA}/page`;
const GUIDE_ROOT = join(REPO, "docs", "guide", AREA);
const CONTENT_ROOT = join(REPO, "yaffo_ui_tests", "user_doc_automation", AREA);
const MARKDOWN = `docs/guide/${AREA}/page.md`;
const WALKTHROUGH = `yaffo_ui_tests/user_doc_automation/${AREA}/page/page.ts`;

let client: {
    setOutputSchema: jest.Mock<(schema: unknown) => void>;
    addUserMessage: jest.Mock<(parts: unknown[]) => void>;
};
let warn: jest.SpiedFunction<typeof console.warn>;
let log: jest.SpiedFunction<typeof console.log>;

const write = (repoRelative: string, content: string): void => {
    const path = resolve(REPO, repoRelative);
    mkdirSync(dirname(path), {recursive: true});
    writeFileSync(path, content, "utf8");
};

const evidence = (over: Partial<Evidence> = {}): Evidence => ({
    page: PAGE,
    target: `${AREA}/assets/page/gallery.webp`,
    baselinePath: "/captures/baseline.webp",
    candidatePath: "/captures/candidate.webp",
    diffSummary: "The Apply Filters button changed.",
    markdown: "Click **Apply Filters**.",
    markdownPath: resolve(REPO, MARKDOWN),
    walkthroughPath: resolve(REPO, WALKTHROUGH),
    covers: "Filtering",
    walkthroughSource: "export default defineWalkthrough({});",
    codeDiff: "+ Apply",
    stringChanges: [{was: "Apply Filters", now: "Apply", source: "en.json"}],
    ...over,
});

const fix = (files: Fix["files"], over: Partial<Fix> = {}): Fix => ({
    files,
    explanation: "Updated the renamed control.",
    confidence: 0.9,
    ...over,
});

const options = (over: Record<string, unknown> = {}) => ({
    toolProviders: [],
    baseUrl: "http://app.test:5002",
    maxFixAttempts: 0,
    ...over,
}) as never;

beforeEach(() => {
    rmSync(GUIDE_ROOT, {recursive: true, force: true});
    rmSync(CONTENT_ROOT, {recursive: true, force: true});
    mkdirSync(dirname(resolve(REPO, MARKDOWN)), {recursive: true});
    mkdirSync(dirname(resolve(REPO, WALKTHROUGH)), {recursive: true});
    client = {
        setOutputSchema: jest.fn(),
        addUserMessage: jest.fn(),
    };
    for (const mock of [toTextPart, runToolLoop, parseAnswer, runGates, revertPage]) {
        mock.mockReset();
    }
    toTextPart.mockImplementation((text) => ({type: "text", text}));
    runToolLoop.mockResolvedValue("answer");
    parseAnswer.mockReturnValue({value: fix([]), errors: []});
    runGates.mockReturnValue([]);
    warn = jest.spyOn(console, "warn").mockImplementation(() => undefined);
    log = jest.spyOn(console, "log").mockImplementation(() => undefined);
});

afterEach(() => {
    warn.mockRestore();
    log.mockRestore();
});

afterAll(() => {
    rmSync(GUIDE_ROOT, {recursive: true, force: true});
    rmSync(CONTENT_ROOT, {recursive: true, force: true});
});

describe("FixSchema", () => {
    it("accepts complete owned-file responses", () => {
        const value = fix([{filename: MARKDOWN, code: "# Updated", description: "Rename"}]);
        expect(FixSchema.parse(value)).toEqual(value);
    });

    it("rejects extra fields and confidence outside zero-to-one", () => {
        expect(FixSchema.safeParse({...fix([]), extra: true}).success).toBe(false);
        expect(FixSchema.safeParse({...fix([]), confidence: 1.1}).success).toBe(false);
        expect(FixSchema.safeParse({...fix([]), confidence: -0.1}).success).toBe(false);
    });
});

describe("applyFix", () => {
    it("sets the schema and gives the existing session the page-specific fix prompt", async () => {
        await applyFix({client} as never, evidence(), options());

        expect(client.setOutputSchema).toHaveBeenCalledWith(FixSchema);
        const prompt = (client.addUserMessage.mock.calls[0][0][0] as {text: string}).text;
        expect(prompt).toContain("http://app.test:5002");
        expect(prompt).toContain(resolve(REPO, MARKDOWN));
        expect(prompt).toContain(resolve(REPO, WALKTHROUGH));
        expect(prompt).toContain("the page says **Apply Filters**; the app now says **Apply**");
        expect(prompt).toContain("Return the complete new contents");
        expect(runToolLoop).toHaveBeenCalledWith(client, []);
        expect(runGates).toHaveBeenCalledWith(PAGE, {useDocker: undefined});
    });

    it("instructs the agent to repair a capture failure instead of changing the guide", async () => {
        await applyFix({client} as never, evidence({
            target: "",
            walkthroughError: "locator('#dialog.active') timed out",
        }), options());

        const prompt = (client.addUserMessage.mock.calls[0][0][0] as {text: string}).text;
        expect(prompt).toContain("Repair the walkthrough");
        expect(prompt).toContain("locator('#dialog.active') timed out");
        expect(prompt).toContain("Do not change the guide prose or its screenshots");
    });

    it("writes both owned trees and updates an existing artifact catalog", async () => {
        const catalog = `yaffo_ui_tests/user_doc_automation/${AREA}/page/page.json`;
        write(catalog, JSON.stringify({
            files: [{filename: MARKDOWN, code: "old", description: "Keep this description"}],
            preserved: true,
        }));
        const returned = fix([
            {filename: MARKDOWN, code: "# Updated page\n"},
            {filename: WALKTHROUGH, code: "export default {};\n", description: "Updated framing"},
        ], {explanation: "Updated page and shot.", confidence: 0.8});
        parseAnswer.mockReturnValue({value: returned, errors: []});

        const result = await applyFix(
            {client} as never,
            evidence(),
            options({toolProviders: [{name: "tools"}], useDocker: true})
        );

        expect(result).toEqual({
            fix: returned,
            attempts: 1,
            written: [MARKDOWN, WALKTHROUGH],
            failures: [],
            reverted: false,
        });
        expect(readFileSync(resolve(REPO, MARKDOWN), "utf8")).toBe("# Updated page\n");
        expect(readFileSync(resolve(REPO, WALKTHROUGH), "utf8")).toBe("export default {};\n");
        expect(runGates).toHaveBeenCalledWith(PAGE, {useDocker: true});
        const updated = JSON.parse(readFileSync(resolve(REPO, catalog), "utf8"));
        expect(updated.preserved).toBe(true);
        expect(updated.explanation).toBe("Updated page and shot.");
        expect(updated.confidence).toBe(0.8);
        expect(updated.files).toEqual([
            {
                filename: MARKDOWN,
                code: "# Updated page\n",
                description: "Keep this description",
            },
            {
                filename: WALKTHROUGH,
                code: "export default {};\n",
                description: "Updated framing",
            },
        ]);
    });

    it("returns a clean no-op when the page needs no files", async () => {
        const returned = fix([], {explanation: "Already accurate"});
        parseAnswer.mockReturnValue({value: returned, errors: []});
        await expect(applyFix({client} as never, evidence(), options())).resolves.toEqual({
            fix: returned,
            attempts: 1,
            written: [],
            failures: [],
            reverted: false,
        });
        expect(revertPage).not.toHaveBeenCalled();
    });

    it("refuses paths outside the two owned trees", async () => {
        const returned = fix([
            {filename: "README.md", code: "overwrite"},
            {filename: "docs/guide/../development/private.md", code: "escape"},
        ]);
        parseAnswer.mockReturnValue({value: returned, errors: []});

        const result = await applyFix({client} as never, evidence(), options());

        expect(result.written).toEqual([]);
        expect(result.failures).toEqual([
            "refused to write outside the agent's trees: README.md",
            "refused to write outside the agent's trees: docs/guide/../development/private.md",
        ]);
        expect(revertPage).not.toHaveBeenCalled();
    });

    it("hands gate failures back in the same session and accepts a correction", async () => {
        const first = fix([{filename: MARKDOWN, code: "bad first answer"}]);
        const second = fix([{filename: MARKDOWN, code: "# Corrected\n"}]);
        runToolLoop.mockResolvedValueOnce("first").mockResolvedValueOnce("second");
        parseAnswer.mockImplementation((_schema, answer) => ({
            value: answer === "first" ? first : second,
            errors: [],
        }));
        runGates.mockReturnValueOnce(["does not typecheck:\nTS1005"])
            .mockReturnValueOnce([]);

        const result = await applyFix(
            {client} as never,
            evidence(),
            options({maxFixAttempts: 1})
        );

        expect(result.attempts).toBe(2);
        expect(result.failures).toEqual([]);
        expect(result.written).toEqual([MARKDOWN]);
        expect(readFileSync(resolve(REPO, MARKDOWN), "utf8")).toBe("# Corrected\n");
        expect(log).toHaveBeenCalledWith(
            "   ↻ attempt 1 failed; handing 1 failure(s) back to the model");
        const retry = (client.addUserMessage.mock.calls[1][0][0] as {text: string}).text;
        expect(retry).toContain("does not typecheck");
        expect(retry).toContain("return the complete files again");
    });

    it("retries malformed output and prefixes parse errors for the model", async () => {
        const corrected = fix([{filename: MARKDOWN, code: "# Corrected\n"}]);
        runToolLoop.mockResolvedValueOnce("bad").mockResolvedValueOnce("good");
        parseAnswer.mockImplementation((_schema, answer) => answer === "bad"
            ? {errors: ["not JSON", "files is required"]}
            : {value: corrected, errors: []});

        const result = await applyFix(
            {client} as never,
            evidence(),
            options({maxFixAttempts: 1})
        );
        expect(result.attempts).toBe(2);
        expect(result.failures).toEqual([]);
        const retry = JSON.stringify(client.addUserMessage.mock.calls[1][0]);
        expect(retry).toContain("malformed response: not JSON");
        expect(retry).toContain("malformed response: files is required");
    });

    it("turns tool-loop exhaustion into a failed fix instead of throwing", async () => {
        runToolLoop.mockRejectedValue("round limit reached");
        runGates.mockReturnValue(["capture failed"]);
        const result = await applyFix({client} as never, evidence(), options());
        expect(result).toMatchObject({
            attempts: 1,
            written: [],
            reverted: false,
            failures: [
                "malformed response: round limit reached",
                "capture failed",
            ],
        });
    });

    it("reverts every path touched by any failed attempt", async () => {
        const first = fix([{filename: MARKDOWN, code: "first attempt"}]);
        runToolLoop.mockResolvedValueOnce("first").mockRejectedValueOnce(new Error("no answer"));
        parseAnswer.mockReturnValue({value: first, errors: []});
        runGates.mockReturnValue(["still broken"]);

        const result = await applyFix(
            {client} as never,
            evidence(),
            options({maxFixAttempts: 1})
        );

        expect(result.written).toEqual([MARKDOWN]);
        expect(result.reverted).toBe(true);
        expect(revertPage).toHaveBeenCalledWith(PAGE, [MARKDOWN]);
    });

    it("leaves failed writes for inspection when rollback is disabled", async () => {
        parseAnswer.mockReturnValue({
            value: fix([{filename: MARKDOWN, code: "failed but inspectable"}]),
            errors: [],
        });
        runGates.mockReturnValue(["mkdocs failed"]);

        const result = await applyFix(
            {client} as never,
            evidence(),
            options({revertOnFailure: false})
        );
        expect(result.reverted).toBe(false);
        expect(revertPage).not.toHaveBeenCalled();
        expect(existsSync(resolve(REPO, MARKDOWN))).toBe(true);
    });

    it("warns about a malformed catalog without undoing a valid edit", async () => {
        const catalog = `yaffo_ui_tests/user_doc_automation/${AREA}/page/page.json`;
        write(catalog, "not JSON");
        parseAnswer.mockReturnValue({
            value: fix([{filename: MARKDOWN, code: "# Valid edit\n"}]),
            errors: [],
        });

        const result = await applyFix({client} as never, evidence(), options());
        expect(result.failures).toEqual([]);
        expect(warn).toHaveBeenCalledWith(
            expect.stringContaining("could not update the catalog"));
        expect(readFileSync(resolve(REPO, MARKDOWN), "utf8")).toBe("# Valid edit\n");
    });

    it("uses the documented default retry budget", () => {
        expect(MAX_FIX_ATTEMPTS).toBe(3);
    });
});
