import {afterEach, beforeEach, describe, expect, it, jest} from "@jest/globals";

const scrubProcessEnv = jest.fn<(extra?: Record<string, string>) => void>();
const loadWalkthroughs = jest.fn<(contentDir: string, only?: string[]) => Promise<unknown[]>>();
const captureWalkthroughs = jest.fn<(walkthroughs: unknown[], options: unknown) => Promise<unknown[]>>();

await jest.unstable_mockModule("../env", () => ({scrubProcessEnv}));
await jest.unstable_mockModule("../paths", () => ({
    BASE_URL: "http://app.test:5002",
    CAPTURE_DIR: "/staging/captures",
    CONTENT_DIR: "/content",
    DOCS_DATA_DIR: "/canonical/yaffo-docs",
}));
await jest.unstable_mockModule("../load", () => ({loadWalkthroughs}));
await jest.unstable_mockModule("../runner", () => ({captureWalkthroughs}));

const {main, runCli} = await import("../capture_worker");
const initialScrubCalls = scrubProcessEnv.mock.calls.map((call) => [...call]);

let log: jest.SpiedFunction<typeof console.log>;
let error: jest.SpiedFunction<typeof console.error>;
const savedExitCode = process.exitCode;

beforeEach(() => {
    loadWalkthroughs.mockReset();
    captureWalkthroughs.mockReset();
    log = jest.spyOn(console, "log").mockImplementation(() => undefined);
    error = jest.spyOn(console, "error").mockImplementation(() => undefined);
});

afterEach(() => {
    process.exitCode = savedExitCode;
    log.mockRestore();
    error.mockRestore();
});

describe("capture worker initialization", () => {
    it("scrubs the environment before any generated walkthrough is loaded", () => {
        expect(initialScrubCalls).toEqual([[{
            DOCS_BASE_URL: "http://app.test:5002",
            YAFFO_DOCS_DATA_DIR: "/canonical/yaffo-docs",
        }]]);
        expect(loadWalkthroughs).not.toHaveBeenCalled();
    });
});

describe("capture worker main", () => {
    it("filters flags out of the requested page list and captures selected walkthroughs", async () => {
        const walkthroughs = [{page: "library/browsing", shots: {}}];
        loadWalkthroughs.mockResolvedValue(walkthroughs);
        captureWalkthroughs.mockResolvedValue([{
            page: "library/browsing",
            shots: [{target: "gallery.webp"}],
        }]);

        await expect(main(["--ignored-flag", "library/browsing"])).resolves.toBe(0);

        expect(loadWalkthroughs).toHaveBeenCalledWith("/content", ["library/browsing"]);
        expect(captureWalkthroughs).toHaveBeenCalledWith(walkthroughs, {
            baseUrl: "http://app.test:5002",
            stagingDir: "/staging/captures",
        });
        expect(log).toHaveBeenCalledWith(
            "Capturing 1 walkthrough(s) from http://app.test:5002");
        expect(log).toHaveBeenCalledWith("  library/browsing: 1 shot(s)");
        expect(error).not.toHaveBeenCalled();
    });

    it.each([
        [[], "No walkthroughs found"],
        [["library/missing"], "No walkthrough for: library/missing"],
    ])("returns failure when selection %j loads nothing", async (args, message) => {
        loadWalkthroughs.mockResolvedValue([]);
        await expect(main(args)).resolves.toBe(1);
        expect(error).toHaveBeenCalledWith(message);
        expect(captureWalkthroughs).not.toHaveBeenCalled();
    });

    it("skips flows when invoked for a stability recapture", async () => {
        const walkthroughs = [{page: "library/browsing", shots: {}}];
        loadWalkthroughs.mockResolvedValue(walkthroughs);
        captureWalkthroughs.mockResolvedValue([{page: "library/browsing", shots: []}]);

        await expect(main(["--shots-only", "library/browsing"])).resolves.toBe(0);

        expect(loadWalkthroughs).toHaveBeenCalledWith("/content", ["library/browsing"]);
        expect(captureWalkthroughs).toHaveBeenCalledWith(walkthroughs, {
            baseUrl: "http://app.test:5002",
            stagingDir: "/staging/captures",
            skipFlows: true,
        });
    });

    it("reports walkthrough failures as captured evidence without failing the worker", async () => {
        loadWalkthroughs.mockResolvedValue([
            {page: "library/good"}, {page: "library/bad"}, {page: "library/worse"},
        ]);
        captureWalkthroughs.mockResolvedValue([
            {page: "library/good", shots: [{}, {}]},
            {page: "library/bad", shots: [], error: "selector missing"},
            {page: "library/worse", shots: [{}], error: "navigation failed"},
        ]);

        await expect(main([])).resolves.toBe(0);

        expect(log).toHaveBeenCalledWith("  library/good: 2 shot(s)");
        expect(error).toHaveBeenCalledWith("  ! selector missing");
        expect(error).toHaveBeenCalledWith("  ! navigation failed");
    });

    it("lets an infrastructure exception reach the direct-run error handler", async () => {
        loadWalkthroughs.mockRejectedValue(new Error("content directory unreadable"));
        await expect(main([])).rejects.toThrow("content directory unreadable");
    });

    it("the direct-run wrapper converts a return code into the process status", async () => {
        loadWalkthroughs.mockResolvedValue([]);
        await runCli([]);
        expect(process.exitCode).toBe(1);
    });

    it("the direct-run wrapper reports rejected orchestration", async () => {
        const failure = new Error("capture infrastructure failed");
        loadWalkthroughs.mockRejectedValue(failure);
        await runCli([]);
        expect(error).toHaveBeenCalledWith(failure);
        expect(process.exitCode).toBe(1);
    });
});
