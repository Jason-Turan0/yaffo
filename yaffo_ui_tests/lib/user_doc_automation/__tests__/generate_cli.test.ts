import {afterAll, afterEach, beforeEach, describe, expect, it, jest} from "@jest/globals";
import {mkdirSync, mkdtempSync, rmSync, writeFileSync} from "fs";
import {tmpdir} from "os";
import {dirname, join} from "path";

const root = mkdtempSync(join(tmpdir(), "yaffo-generate-cli-"));
const CONTENT_DIR = join(root, "content");
const GUIDE_DIR = join(root, "guide");
const RUN_LOG_DIR = join(root, "logs", "run");
const BASE_URL = "http://app.test:5002";

const createFilesystemClient = jest.fn<(...args: unknown[]) => Promise<unknown>>();
const createPlaywrightClient = jest.fn<(...args: unknown[]) => Promise<unknown>>();
const localFilesystemMemoryToolFactory = jest.fn<(pageDir: string) => unknown>();
const newRunLogDir = jest.fn(() => RUN_LOG_DIR);
const verifyBrowserTool = jest.fn<(...args: unknown[]) => Promise<void>>();
const gatherSandboxFacts = jest.fn<(baseUrl: string) => Promise<unknown>>();
const describeSandboxFacts = jest.fn<(facts: unknown) => string>();
const generateWalkthrough = jest.fn<(...args: unknown[]) => Promise<unknown>>();
const runGates = jest.fn<(page: string, options?: unknown) => string[]>();
const revertPage = jest.fn<(page: string, written: string[]) => void>();

await jest.unstable_mockModule("@lib/tool_providers/mcp_filesystem_client", () => ({createFilesystemClient}));
await jest.unstable_mockModule("@lib/tool_providers/mcp_playwright_client", () => ({createPlaywrightClient}));
await jest.unstable_mockModule("@lib/tool_providers/local_filesystem_memory_tool", () => ({
    localFilesystemMemoryToolFactory,
}));
await jest.unstable_mockModule("@lib/types", () => ({YAFFO_APP_ROOT: "/app/yaffo"}));
await jest.unstable_mockModule("../paths", () => ({
    BASE_URL, CONTENT_DIR, GUIDE_DIR, newRunLogDir,
}));
await jest.unstable_mockModule("../preflight", () => ({verifyBrowserTool}));
await jest.unstable_mockModule("../sandbox_facts", () => ({
    describeSandboxFacts, gatherSandboxFacts,
}));
await jest.unstable_mockModule("../generate", () => ({generateWalkthrough}));
await jest.unstable_mockModule("../gates", () => ({revertPage, runGates}));

const {main, pagesNeedingOne, runCli} = await import("../generate_cli");

let filesystem: {disconnect: jest.Mock<() => Promise<void>>};
let browser: {disconnect: jest.Mock<() => Promise<void>>};
let memory: {disconnect: jest.Mock<() => Promise<void>>};
let log: jest.SpiedFunction<typeof console.log>;
let error: jest.SpiedFunction<typeof console.error>;
const savedExitCode = process.exitCode;

const write = (path: string, content: string): void => {
    mkdirSync(dirname(path), {recursive: true});
    writeFileSync(path, content, "utf8");
};

const writeSpec = (body: string): void => {
    write(join(CONTENT_DIR, "spec.yaml"), `version: 1\npages:\n${body}`);
};

beforeEach(() => {
    rmSync(CONTENT_DIR, {recursive: true, force: true});
    rmSync(GUIDE_DIR, {recursive: true, force: true});
    mkdirSync(CONTENT_DIR, {recursive: true});
    mkdirSync(GUIDE_DIR, {recursive: true});
    filesystem = {disconnect: jest.fn(async () => undefined)};
    browser = {disconnect: jest.fn(async () => undefined)};
    memory = {disconnect: jest.fn(async () => undefined)};
    for (const mock of [
        createFilesystemClient, createPlaywrightClient, localFilesystemMemoryToolFactory,
        newRunLogDir, verifyBrowserTool, gatherSandboxFacts, describeSandboxFacts,
        generateWalkthrough, runGates, revertPage,
    ]) mock.mockReset();
    createFilesystemClient.mockResolvedValue(filesystem);
    createPlaywrightClient.mockResolvedValue(browser);
    localFilesystemMemoryToolFactory.mockReturnValue(memory);
    newRunLogDir.mockReturnValue(RUN_LOG_DIR);
    verifyBrowserTool.mockResolvedValue(undefined);
    gatherSandboxFacts.mockResolvedValue({photos: ["photo.jpg"], videos: []});
    describeSandboxFacts.mockReturnValue("sandbox facts");
    generateWalkthrough.mockResolvedValue({written: ["walkthrough.ts"], errors: [], attempts: 1});
    runGates.mockReturnValue([]);
    log = jest.spyOn(console, "log").mockImplementation(() => undefined);
    error = jest.spyOn(console, "error").mockImplementation(() => undefined);
});

afterEach(() => {
    process.exitCode = savedExitCode;
    log.mockRestore();
    error.mockRestore();
});

afterAll(() => {
    rmSync(root, {recursive: true, force: true});
});

describe("pagesNeedingOne", () => {
    it("selects observable pages without walkthroughs", () => {
        write(join(CONTENT_DIR, "area", "existing", "existing.ts"), "export default {};");
        expect(pagesNeedingOne({
            "area/missing": {walkthrough: true},
            "area/existing": {walkthrough: true},
            "area/never": {walkthrough: false},
        })).toEqual(["area/missing"]);
    });
});

describe("generation CLI", () => {
    it("returns cleanly when every page already has a walkthrough", async () => {
        writeSpec("  area/never:\n    walkthrough: false\n");
        await expect(main([])).resolves.toBe(0);
        expect(log).toHaveBeenCalledWith("Every page already has a walkthrough.");
        expect(createFilesystemClient).not.toHaveBeenCalled();
    });

    it("keeps the first named page when no --model option is present", async () => {
        writeSpec("  area/page:\n    walkthrough: true\n    covers: Page charter\n");

        await expect(main(["area/page"])).resolves.toBe(0);

        expect(generateWalkthrough).toHaveBeenCalledTimes(1);
        expect(generateWalkthrough.mock.calls[0][0]).toBe("area/page");
        expect((generateWalkthrough.mock.calls[0][1] as {model?: string}).model).toBeUndefined();
    });

    it("creates investigation providers, preflights the browser, and passes runtime facts", async () => {
        writeSpec("  area/page:\n    walkthrough: true\n    covers: Page charter\n");
        write(join(CONTENT_DIR, "area", "page", "memories", "note.md"), "remember");

        await main(["--model", "vision-model", "--docker", "area/page"]);

        expect(createFilesystemClient).toHaveBeenCalledWith(
            ["/app/yaffo", GUIDE_DIR, CONTENT_DIR], {readonly: false});
        expect(createPlaywrightClient).toHaveBeenCalledWith(expect.objectContaining({
            headless: true,
            baseUrl: BASE_URL,
            browser: "chromium",
            artifacts: {outputDir: RUN_LOG_DIR, saveVideo: false, saveSession: false},
        }));
        expect(verifyBrowserTool).toHaveBeenCalledWith([filesystem, browser], BASE_URL);
        expect(gatherSandboxFacts).toHaveBeenCalledWith(BASE_URL);
        expect(localFilesystemMemoryToolFactory).toHaveBeenCalledWith(
            join(CONTENT_DIR, "area", "page"));
        const generatedOptions = generateWalkthrough.mock.calls[0][1] as Record<string, unknown>;
        expect(generatedOptions).toMatchObject({
            model: "vision-model",
            covers: "Page charter",
            sandboxFacts: "sandbox facts",
            hasMemories: true,
        });
        expect(generatedOptions.toolProviders).toEqual([filesystem, browser, memory]);
        expect(filesystem.disconnect).toHaveBeenCalledTimes(1);
        expect(browser.disconnect).toHaveBeenCalledTimes(1);
    });

    it("wires verification into the generation retry loop", async () => {
        writeSpec("  area/page:\n    walkthrough: true\n");
        generateWalkthrough.mockImplementation(async (_page, rawOptions) => {
            const generatedOptions = rawOptions as {verify: (written: string[]) => string[]};
            expect(generatedOptions.verify(["a.md", "a.ts"])).toEqual(["capture failed"]);
            return {written: ["a.md", "a.ts"], errors: [], attempts: 2};
        });
        runGates.mockReturnValue(["capture failed"]);

        await main(["--docker", "area/page"]);

        expect(runGates).toHaveBeenCalledWith("area/page", {useDocker: true});
        expect(log).toHaveBeenCalledWith("   wrote a.md");
        expect(log).toHaveBeenCalledWith("   (2 attempts)");
    });

    it("rolls back rejected output but not an answer that wrote nothing", async () => {
        writeSpec([
            "  area/first: {walkthrough: true}",
            "  area/second: {walkthrough: true}",
            "",
        ].join("\n"));
        generateWalkthrough
            .mockResolvedValueOnce({written: ["first.ts"], errors: ["bad capture"], attempts: 1})
            .mockResolvedValueOnce({written: [], errors: ["not JSON"], attempts: 1});

        await expect(main([])).resolves.toBe(1);

        expect(revertPage).toHaveBeenCalledWith("area/first", ["first.ts"]);
        expect(revertPage).toHaveBeenCalledTimes(1);
        expect(error).toHaveBeenCalledWith("   → rolled back");
        expect(error).toHaveBeenCalledWith("   ‼️  not JSON");
    });

    it("disconnects providers when browser preflight fails", async () => {
        writeSpec("  area/page: {walkthrough: true}\n");
        verifyBrowserTool.mockRejectedValue(new Error("browser missing"));

        await expect(main([])).rejects.toThrow("browser missing");

        expect(filesystem.disconnect).toHaveBeenCalledTimes(1);
        expect(browser.disconnect).toHaveBeenCalledTimes(1);
    });

    it("the direct wrapper reports failures and sets the exit code", async () => {
        writeSpec("  area/page: {walkthrough: true}\n");
        gatherSandboxFacts.mockRejectedValue(new Error("sandbox unavailable"));
        await runCli([]);
        expect(process.exitCode).toBe(1);
        expect(error).toHaveBeenCalledWith(expect.objectContaining({message: "sandbox unavailable"}));
    });
});
