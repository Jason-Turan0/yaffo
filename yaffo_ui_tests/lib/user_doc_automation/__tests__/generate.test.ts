import {afterAll, afterEach, beforeEach, describe, expect, it, jest} from "@jest/globals";
import {mkdirSync, readFileSync, rmSync, writeFileSync} from "fs";
import {dirname, join, resolve} from "path";
import type {Generated} from "../generate";

const createModelClient = jest.fn<(...args: unknown[]) => unknown>();
const supportsNativeStructuredOutput = jest.fn<(model: string) => boolean>();
const supportsVision = jest.fn<(model: string) => boolean>();
const visionModelFor = jest.fn<(model: string) => string>();
const toTextPart = jest.fn((text: string) => ({type: "text", text}));
const runToolLoop = jest.fn<(...args: unknown[]) => Promise<string>>();
const parseAnswer = jest.fn<(...args: unknown[]) => {value?: Generated; errors: string[]}>();

await jest.unstable_mockModule("@lib/model_clients/model_client_factory", () => ({
    createModelClient,
    DEFAULT_MODEL: "default-model",
    supportsNativeStructuredOutput,
}));
await jest.unstable_mockModule("@lib/model_clients/model_client.interface", () => ({
    supportsVision,
    toTextPart,
    visionModelFor,
}));
await jest.unstable_mockModule("../tool_loop", () => ({parseAnswer, runToolLoop}));

const {
    generateWalkthrough,
    GenerateSchema,
    GENERATE_MAX_OUTPUT_TOKENS,
    MAX_FIX_ATTEMPTS,
    requiredShots,
} = await import("../generate");

const REPO = resolve(join(process.cwd(), ".."));
const AREA = `__generate_unit_${process.pid}_${Date.now()}`;
const PAGE = `${AREA}/page`;
const GUIDE_DIR = join(REPO, "docs", "guide");
const CONTENT_DIR = join(REPO, "yaffo_ui_tests", "user_doc_automation");
const GUIDE_ROOT = join(GUIDE_DIR, AREA);
const CONTENT_ROOT = join(CONTENT_DIR, AREA);
const MARKDOWN = `docs/guide/${AREA}/page.md`;
const WALKTHROUGH = `yaffo_ui_tests/user_doc_automation/${AREA}/page/page.ts`;

let client: {
    setMaxOutputTokens: jest.Mock<(tokens: number) => void>;
    addUserMessage: jest.Mock<(parts: unknown[]) => void>;
};
let log: jest.SpiedFunction<typeof console.log>;

const write = (path: string, content: string): void => {
    mkdirSync(dirname(path), {recursive: true});
    writeFileSync(path, content, "utf8");
};

const generated = (files: Generated["files"], over: Partial<Generated> = {}): Generated => ({
    files,
    pageContext: "Pin the gallery view in the URL.",
    explanation: "Inspected the route and browser.",
    confidence: 0.8,
    ...over,
});

const options = (over: Record<string, unknown> = {}) => ({
    runLogDir: "/logs",
    toolProviders: [],
    baseUrl: "http://app.test:5002",
    guideDir: GUIDE_DIR,
    contentDir: CONTENT_DIR,
    hasMemories: false,
    maxFixAttempts: 0,
    ...over,
}) as never;

beforeEach(() => {
    rmSync(GUIDE_ROOT, {recursive: true, force: true});
    rmSync(CONTENT_ROOT, {recursive: true, force: true});
    write(resolve(REPO, MARKDOWN), [
        "# Page",
        "",
        "![Gallery with filters](assets/page/gallery.webp)",
        "![Detail](assets/page/detail.png)",
        "",
    ].join("\n"));
    client = {
        setMaxOutputTokens: jest.fn(),
        addUserMessage: jest.fn(),
    };
    for (const mock of [
        createModelClient, supportsNativeStructuredOutput, supportsVision,
        visionModelFor, toTextPart, runToolLoop, parseAnswer,
    ]) mock.mockReset();
    createModelClient.mockReturnValue(client);
    supportsNativeStructuredOutput.mockReturnValue(true);
    supportsVision.mockReturnValue(true);
    visionModelFor.mockImplementation((model) => model);
    toTextPart.mockImplementation((text) => ({type: "text", text}));
    runToolLoop.mockResolvedValue("answer");
    parseAnswer.mockReturnValue({value: generated([]), errors: []});
    log = jest.spyOn(console, "log").mockImplementation(() => undefined);
});

afterEach(() => {
    log.mockRestore();
});

afterAll(() => {
    rmSync(GUIDE_ROOT, {recursive: true, force: true});
    rmSync(CONTENT_ROOT, {recursive: true, force: true});
});

describe("requiredShots", () => {
    it("extracts captions and basenames in Markdown order", () => {
        expect(requiredShots([
            "![Gallery](assets/page/gallery.webp)",
            "![Map view](../other/map.png)",
        ].join("\n"))).toEqual([
            {alt: "Gallery", filename: "gallery.webp"},
            {alt: "Map view", filename: "map.png"},
        ]);
    });

    it("returns an empty list for prose without images", () => {
        expect(requiredShots("# Page\n\nNothing visual.")).toEqual([]);
    });
});

describe("GenerateSchema", () => {
    it("accepts complete generated artifacts", () => {
        const value = generated([{filename: MARKDOWN, code: "# Page", description: "Guide"}]);
        expect(GenerateSchema.parse(value)).toEqual(value);
    });

    it("rejects extra file fields and out-of-range confidence", () => {
        expect(GenerateSchema.safeParse(generated([
            {filename: MARKDOWN, code: "x", extra: true} as never,
        ])).success).toBe(false);
        expect(GenerateSchema.safeParse(generated([], {confidence: 2})).success).toBe(false);
    });
});

describe("generateWalkthrough", () => {
    it("returns before opening a model when the guide page is absent", async () => {
        rmSync(resolve(REPO, MARKDOWN));
        await expect(generateWalkthrough(PAGE, options())).resolves.toEqual({
            written: [], errors: [`no such page: ${PAGE}.md`], attempts: 0,
        });
        expect(createModelClient).not.toHaveBeenCalled();
    });

    it("refuses a model that cannot judge screenshot framing", async () => {
        visionModelFor.mockReturnValue("text-only");
        supportsVision.mockReturnValue(false);
        await expect(generateWalkthrough(PAGE, options({model: "requested"}))).resolves.toEqual({
            written: [], errors: ["text-only cannot see, so it cannot judge framing"], attempts: 0,
        });
    });

    it("builds a grounded prompt, flattens tools, and sets the large output budget", async () => {
        supportsNativeStructuredOutput.mockReturnValue(false);
        visionModelFor.mockReturnValue("vision-model");
        const first = {getTools: () => [{name: "read_file"}]};
        const second = {getTools: () => [{name: "browser_navigate"}]};

        await generateWalkthrough(PAGE, options({
            model: "requested",
            toolProviders: [first, second],
            covers: "Every filter and its empty state",
            sandboxFacts: "## Sandbox\n\n- photo.jpg",
            hasMemories: true,
        }));

        expect(visionModelFor).toHaveBeenCalledWith("requested");
        expect(createModelClient).toHaveBeenCalledWith(
            "/logs",
            "vision-model",
            expect.stringContaining("Respond with JSON matching this schema"),
            [{name: "read_file"}, {name: "browser_navigate"}],
            GenerateSchema,
        );
        expect(client.setMaxOutputTokens).toHaveBeenCalledWith(GENERATE_MAX_OUTPUT_TOKENS);
        expect(runToolLoop).toHaveBeenCalledWith(client, [first, second], 45);
        const prompt = (client.addUserMessage.mock.calls[0][0][0] as {text: string}).text;
        expect(prompt).toContain("Every filter and its empty state");
        expect(prompt).toContain("`gallery.webp` — Gallery with filters");
        expect(prompt).toContain("## Sandbox");
        expect(prompt).toContain("**Read them first**");
        expect(prompt).toContain(MARKDOWN);
        expect(prompt).toContain(WALKTHROUGH);
    });

    it("writes owned outputs, verifies them, and seeds the artifact catalog", async () => {
        const answer = generated([
            {filename: MARKDOWN, code: "# Updated page\n", description: "Guide"},
            {filename: WALKTHROUGH, code: "export default {};\n", description: "Capture"},
        ]);
        parseAnswer.mockReturnValue({value: answer, errors: []});
        const verify = jest.fn((_written: string[]) => []);

        const result = await generateWalkthrough(PAGE, options({verify}));

        expect(result).toEqual({generated: answer, written: [MARKDOWN, WALKTHROUGH], errors: [], attempts: 1});
        expect(verify).toHaveBeenCalledWith([MARKDOWN, WALKTHROUGH]);
        expect(readFileSync(resolve(REPO, MARKDOWN), "utf8")).toBe("# Updated page\n");
        expect(readFileSync(resolve(REPO, WALKTHROUGH), "utf8")).toBe("export default {};\n");
        const catalog = JSON.parse(readFileSync(
            join(CONTENT_ROOT, "page", "page.json"), "utf8"));
        expect(catalog).toEqual({
            files: answer.files,
            pageContext: answer.pageContext,
            explanation: answer.explanation,
            confidence: answer.confidence,
        });
    });

    it("refuses files outside this page, including path-prefix lookalikes", async () => {
        const siblingPrefix = `yaffo_ui_tests/user_doc_automation/${AREA}/page-evil/evil.ts`;
        parseAnswer.mockReturnValue({value: generated([
            {filename: "README.md", code: "bad"},
            {filename: siblingPrefix, code: "bad"},
        ]), errors: []});

        const result = await generateWalkthrough(PAGE, options());
        expect(result.written).toEqual([]);
        expect(result.errors).toEqual([
            "refused to write outside the page's own files: README.md",
            `refused to write outside the page's own files: ${siblingPrefix}`,
        ]);
    });

    it("hands verification failures back and accepts the corrected complete files", async () => {
        const first = generated([{filename: WALKTHROUGH, code: "bad"}]);
        const second = generated([{filename: WALKTHROUGH, code: "corrected"}]);
        runToolLoop.mockResolvedValueOnce("first").mockResolvedValueOnce("second");
        parseAnswer.mockImplementation((_schema, answer) => ({
            value: answer === "first" ? first : second,
            errors: [],
        }));
        const verify = jest.fn()
            .mockReturnValueOnce(["does not typecheck: TS1005"])
            .mockReturnValueOnce([]);

        const result = await generateWalkthrough(PAGE, options({verify, maxFixAttempts: 1}));
        expect(result.attempts).toBe(2);
        expect(result.errors).toEqual([]);
        expect(readFileSync(resolve(REPO, WALKTHROUGH), "utf8")).toBe("corrected");
        expect(log).toHaveBeenCalledWith(
            "   ↻ attempt 1 failed; handing 1 failure(s) back to the model");
        expect(JSON.stringify(client.addUserMessage.mock.calls[1][0])).toContain("TS1005");
    });

    it("retries parse failures in the same session", async () => {
        const corrected = generated([{filename: WALKTHROUGH, code: "corrected"}]);
        runToolLoop.mockResolvedValueOnce("bad").mockResolvedValueOnce("good");
        parseAnswer.mockImplementation((_schema, answer) => answer === "bad"
            ? {errors: ["not JSON"]}
            : {value: corrected, errors: []});
        const result = await generateWalkthrough(PAGE, options({maxFixAttempts: 1}));
        expect(result.attempts).toBe(2);
        expect(result.errors).toEqual([]);
    });

    it("reports tool-loop exhaustion without losing paths touched by earlier attempts", async () => {
        const first = generated([{filename: WALKTHROUGH, code: "first attempt"}]);
        runToolLoop.mockResolvedValueOnce("first").mockRejectedValueOnce(new Error("round limit"));
        parseAnswer.mockReturnValue({value: first, errors: []});
        const verify = jest.fn(() => ["capture failed"]);

        const result = await generateWalkthrough(PAGE, options({
            verify, maxFixAttempts: 1,
        }));
        expect(result.attempts).toBe(2);
        expect(result.written).toEqual([WALKTHROUGH]);
        expect(result.errors).toEqual(["round limit"]);
    });

    it("uses the documented retry default and native structured output path", async () => {
        expect(MAX_FIX_ATTEMPTS).toBe(3);
        await generateWalkthrough(PAGE, options());
        expect(createModelClient.mock.calls[0][2]).not.toContain(
            "Respond with JSON matching this schema");
    });
});
