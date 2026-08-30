import {afterEach, beforeEach, describe, expect, it, jest} from "@jest/globals";
import {mkdtempSync, rmSync, writeFileSync} from "fs";
import {tmpdir} from "os";
import {join} from "path";
import type {Evidence} from "../evidence";
import type {Triage} from "../triage";

const createModelClient = jest.fn<(...args: unknown[]) => unknown>();
const supportsNativeStructuredOutput = jest.fn<(model: string) => boolean>();
const supportsVision = jest.fn<(model: string) => boolean>();
const visionModelFor = jest.fn<(model: string) => string>();
const toTextPart = jest.fn((text: string) => ({type: "text", text}));
const toImagePart = jest.fn((bytes: Uint8Array, mimeType: string) => ({
    type: "image", bytes: [...bytes], mimeType,
}));
const runToolLoop = jest.fn<(client: unknown, providers: unknown[]) => Promise<string>>();
const parseAnswer = jest.fn<(...args: unknown[]) => {value?: Triage; errors: string[]}>();

await jest.unstable_mockModule("@lib/model_clients/model_client_factory", () => ({
    createModelClient,
    DEFAULT_MODEL: "default-model",
    supportsNativeStructuredOutput,
}));
await jest.unstable_mockModule("@lib/model_clients/model_client.interface", () => ({
    supportsVision,
    toImagePart,
    toTextPart,
    visionModelFor,
}));
await jest.unstable_mockModule("../tool_loop", () => ({parseAnswer, runToolLoop}));

const {openSession, triageShot, TriageSchema} = await import("../triage");

let testDir: string;
let client: {
    addUserMessage: jest.Mock<(parts: unknown[]) => void>;
};
let log: jest.SpiedFunction<typeof console.log>;

const provider = (name: string, tools: unknown[]) => ({
    getTools: jest.fn(() => tools),
    callTool: jest.fn(),
    name,
});

const evidence = (over: Partial<Evidence> = {}): Evidence => ({
    page: "library/browsing",
    target: "library/assets/browsing/gallery.webp",
    baselinePath: join(testDir, "baseline.webp"),
    candidatePath: join(testDir, "candidate.webp"),
    overlayPath: join(testDir, "overlay.png"),
    diffSummary: "321 pixels differ, bounded by the filter button.",
    markdown: "Click **Apply Filters** to update the gallery.",
    markdownPath: "/repo/docs/guide/library/browsing.md",
    walkthroughPath: "/repo/yaffo_ui_tests/user_doc_automation/library/browsing/browsing.ts",
    covers: "Filtering the gallery",
    walkthroughSource: "export default defineWalkthrough({page: 'library/browsing'});",
    codeDiff: "- Apply Filters\n+ Apply",
    stringChanges: [
        {was: "Apply Filters", now: "Apply", source: "en.json"},
        {was: "Clear Filters", source: "messages.pot"},
    ],
    ...over,
});

const classified: Triage = {
    classification: "intended_change",
    confidence: "high",
    summary: "The filter button was renamed.",
    reasoning: "The new capture matches the dependency diff.",
    proseImpact: [{quote: "Click Apply Filters", issue: "The control is now Apply"}],
    recommendedAction: "promote",
};

beforeEach(() => {
    testDir = mkdtempSync(join(tmpdir(), "yaffo-triage-"));
    writeFileSync(join(testDir, "baseline.webp"), Buffer.from([1, 2, 3]));
    writeFileSync(join(testDir, "candidate.webp"), Buffer.from([4, 5]));
    writeFileSync(join(testDir, "overlay.png"), Buffer.from([6, 7, 8, 9]));
    client = {addUserMessage: jest.fn()};
    for (const mock of [
        createModelClient, supportsNativeStructuredOutput, supportsVision,
        visionModelFor, toTextPart, toImagePart, runToolLoop, parseAnswer,
    ]) mock.mockReset();
    createModelClient.mockReturnValue(client);
    supportsNativeStructuredOutput.mockReturnValue(true);
    supportsVision.mockReturnValue(true);
    visionModelFor.mockImplementation((model) => model);
    toTextPart.mockImplementation((text) => ({type: "text", text}));
    toImagePart.mockImplementation((bytes, mimeType) => ({
        type: "image", bytes: [...bytes], mimeType,
    }));
    runToolLoop.mockResolvedValue("model answer");
    parseAnswer.mockReturnValue({value: classified, errors: []});
    log = jest.spyOn(console, "log").mockImplementation(() => undefined);
});

afterEach(() => {
    log.mockRestore();
    rmSync(testDir, {recursive: true, force: true});
});

describe("TriageSchema", () => {
    it("accepts the complete classification contract", () => {
        expect(TriageSchema.parse(classified)).toEqual(classified);
    });

    it("rejects classifications and actions outside the fixed vocabulary", () => {
        expect(TriageSchema.safeParse({...classified, classification: "maybe"}).success).toBe(false);
        expect(TriageSchema.safeParse({...classified, recommendedAction: "ignore"}).success).toBe(false);
    });
});

describe("openSession", () => {
    it("opens the vision-capable model with flattened tools and textual evidence", () => {
        visionModelFor.mockReturnValue("vision-default");
        const first = provider("one", [{name: "read_file"}]);
        const second = provider("two", [{name: "browser_navigate"}]);

        const session = openSession(evidence(), {
            runLogDir: "/logs",
            toolProviders: [first, second] as never,
        });

        expect(session).toEqual({client, model: "vision-default"});
        expect(visionModelFor).toHaveBeenCalledWith("default-model");
        expect(createModelClient).toHaveBeenCalledWith(
            "/logs",
            "vision-default",
            expect.stringContaining("You triage screenshot changes"),
            [{name: "read_file"}, {name: "browser_navigate"}],
            TriageSchema,
        );
        expect(createModelClient.mock.calls[0][2]).toContain("at most 0.1% of pixels changed");
        expect(createModelClient.mock.calls[0][2]).toContain("recommend promote");
        const prompt = (client.addUserMessage.mock.calls[0][0][0] as {text: string}).text;
        expect(prompt).toContain("What this page is obliged to cover");
        expect(prompt).toContain('"Apply Filters" is now "Apply"');
        expect(prompt).toContain('"Clear Filters" no longer exists');
        expect(toImagePart).not.toHaveBeenCalled();
    });

    it("supports a requested model and no tool providers", () => {
        openSession(evidence({covers: undefined, stringChanges: []}), {
            model: "requested" as never,
            runLogDir: "/logs",
        });
        expect(visionModelFor).toHaveBeenCalledWith("requested");
        expect(createModelClient.mock.calls[0][3]).toEqual([]);
        const prompt = (client.addUserMessage.mock.calls[0][0][0] as {text: string}).text;
        expect(prompt).not.toContain("What this page is obliged to cover");
        expect(prompt).not.toContain("Controls this page names");
    });

    it("describes a thrown walkthrough as non-visual repair evidence", () => {
        openSession(evidence({
            target: "",
            walkthroughError: "locator.waitFor timed out",
            diffSummary: "Walkthrough failed before capture completed.",
        }), {runLogDir: "/logs"});

        const prompt = (client.addUserMessage.mock.calls[0][0][0] as {text: string}).text;
        expect(prompt).toContain("failed before it completed");
        expect(prompt).toContain("walkthrough defect to repair");
        expect(prompt).not.toContain("The images follow");
    });
});

describe("triageShot", () => {
    it("labels and attaches baseline, candidate, and overlay images", async () => {
        const tools = provider("tools", [{name: "read_file"}]);
        const session = await triageShot(evidence(), {
            model: "vision-model" as never,
            runLogDir: "/logs",
            toolProviders: [tools] as never,
        });

        expect(session).toEqual({triage: classified, client, model: "vision-model"});
        expect(runToolLoop).toHaveBeenCalledWith(client, [tools]);
        expect(parseAnswer).toHaveBeenCalledWith(TriageSchema, "model answer");
        expect(toImagePart.mock.calls).toEqual([
            [expect.any(Uint8Array), "image/webp"],
            [expect.any(Uint8Array), "image/webp"],
            [expect.any(Uint8Array), "image/png"],
        ]);
        const parts = client.addUserMessage.mock.calls[0][0] as Array<{type: string; text?: string}>;
        expect(parts.map((part) => part.text).filter(Boolean)).toEqual(expect.arrayContaining([
            "Committed baseline:",
            "New capture:",
            "Diff overlay (magenta = changed pixels):",
        ]));
    });

    it("omits the overlay pair when no diff image exists", async () => {
        await triageShot(evidence({overlayPath: undefined}), {runLogDir: "/logs"});
        expect(toImagePart).toHaveBeenCalledTimes(2);
        const text = JSON.stringify(client.addUserMessage.mock.calls[0][0]);
        expect(text).not.toContain("Diff overlay");
    });

    it("substitutes a vision model and explains the substitution", async () => {
        visionModelFor.mockReturnValue("vision-fallback");
        await triageShot(evidence(), {
            model: "text-only" as never,
            runLogDir: "/logs",
        });
        expect(log).toHaveBeenCalledWith(
            "  text-only cannot receive images; using vision-fallback instead.");
        expect(createModelClient.mock.calls[0][1]).toBe("vision-fallback");
    });

    it("refuses to classify without a vision-capable model", async () => {
        supportsVision.mockReturnValue(false);
        await expect(triageShot(evidence(), {
            model: "text-only" as never,
            runLogDir: "/logs",
        })).rejects.toThrow(/cannot receive images.*seeing it/s);
        expect(createModelClient).not.toHaveBeenCalled();
    });

    it("adds the JSON schema for providers without native structured output", async () => {
        supportsNativeStructuredOutput.mockReturnValue(false);
        await triageShot(evidence(), {runLogDir: "/logs"});
        const systemPrompt = createModelClient.mock.calls[0][2] as string;
        expect(systemPrompt).toContain("Respond with JSON matching this schema");
        expect(systemPrompt).toContain('"classification"');
    });

    it("does not duplicate schema instructions for native structured output", async () => {
        await triageShot(evidence(), {runLogDir: "/logs"});
        expect(createModelClient.mock.calls[0][2]).not.toContain(
            "Respond with JSON matching this schema");
    });

    it("reports every parse error when the model answer is unusable", async () => {
        parseAnswer.mockReturnValue({errors: ["not JSON", "classification is required"]});
        await expect(triageShot(evidence(), {runLogDir: "/logs"}))
            .rejects.toThrow(
                "unusable triage response — not JSON; classification is required");
    });
});
