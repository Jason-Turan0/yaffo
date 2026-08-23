import {afterAll, afterEach, beforeEach, describe, expect, it, jest} from "@jest/globals";
import {createHash} from "crypto";
import {mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync} from "fs";
import {tmpdir} from "os";
import {dirname, join, resolve} from "path";

const suiteRoot = mkdtempSync(join(tmpdir(), "yaffo-docs-capture-"));
const REPO = join(suiteRoot, "repo");
const UI_TESTS = join(REPO, "yaffo_ui_tests");
const CONTENT_DIR = join(UI_TESTS, "user_doc_automation");
const CAPTURE_DIR = join(UI_TESTS, ".doc-staging", "captures");
const GUIDE_DIR = join(REPO, "docs", "guide");
const BASE_URL = "http://app.test:5002";
const HOST_REPO = resolve(join(process.cwd(), ".."));

const scrubProcessEnv = jest.fn<(extra?: Record<string, string>) => void>();
const dockerAvailable = jest.fn<(dockerEnv?: Record<string, string>) => boolean>();
const runCaptureContainer = jest.fn<(options: unknown) => number>();
const snapshotDockerEnv = jest.fn<() => Record<string, string>>();
const loadWalkthroughs = jest.fn<(contentDir: string, only?: string[]) => Promise<unknown[]>>();
const processResults = jest.fn<(raw: unknown[], options: unknown) => unknown[]>();
const runWalkthroughs = jest.fn<(
    walkthroughs: unknown[],
    options: unknown
) => Promise<unknown[]>>();

snapshotDockerEnv.mockReturnValue({DOCKER_HOST: "unix:///initial.sock"});

await jest.unstable_mockModule("../env", () => ({scrubProcessEnv}));
await jest.unstable_mockModule("../docker", () => ({
    DOCS_CAPTURE_IMAGE: "yaffo-docs-capture:latest",
    dockerAvailable,
    runCaptureContainer,
    snapshotDockerEnv,
}));
await jest.unstable_mockModule("../load", () => ({loadWalkthroughs}));
await jest.unstable_mockModule("../paths", () => ({
    BASE_URL, CAPTURE_DIR, CONTENT_DIR, GUIDE_DIR,
}));
await jest.unstable_mockModule("../runner", () => ({
    processResults,
    RAW_FILENAME: "raw.json",
    runWalkthroughs,
}));

const {main, runCli} = await import("../docs_capture");
const initialization = {
    dockerEnv: snapshotDockerEnv.mock.results[0]?.value,
    snapshotOrder: snapshotDockerEnv.mock.invocationCallOrder[0],
    scrubOrder: scrubProcessEnv.mock.invocationCallOrder[0],
    scrubArgs: scrubProcessEnv.mock.calls[0],
};

let log: jest.SpiedFunction<typeof console.log>;
let error: jest.SpiedFunction<typeof console.error>;
const savedExitCode = process.exitCode;

const observed = (serverObserver: "recorded" | "unavailable" = "recorded") => ({
    page: "library/browsing",
    urls: ["/", "/settings"],
    static: ["yaffo/static/app.js"],
    templates: ["yaffo/templates/home.html"],
    routes: ["yaffo/routes/home.py"],
    serverObserver,
});

const result = (over: Record<string, unknown> = {}) => ({
    page: "library/browsing",
    shots: [],
    observation: observed(),
    ...over,
});

const write = (path: string, content: string): void => {
    mkdirSync(dirname(path), {recursive: true});
    writeFileSync(path, content, "utf8");
};

beforeEach(() => {
    rmSync(REPO, {recursive: true, force: true});
    mkdirSync(CONTENT_DIR, {recursive: true});
    mkdirSync(CAPTURE_DIR, {recursive: true});
    mkdirSync(GUIDE_DIR, {recursive: true});
    for (const mock of [
        dockerAvailable, runCaptureContainer, loadWalkthroughs, processResults, runWalkthroughs,
    ]) mock.mockReset();
    dockerAvailable.mockReturnValue(true);
    runCaptureContainer.mockReturnValue(0);
    processResults.mockReturnValue([]);
    runWalkthroughs.mockResolvedValue([]);
    log = jest.spyOn(console, "log").mockImplementation(() => undefined);
    error = jest.spyOn(console, "error").mockImplementation(() => undefined);
});

afterEach(() => {
    process.exitCode = savedExitCode;
    log.mockRestore();
    error.mockRestore();
});

afterAll(() => {
    rmSync(suiteRoot, {recursive: true, force: true});
});

describe("run initialization", () => {
    it("snapshots Docker settings before scrubbing generated-code credentials", () => {
        expect(initialization.dockerEnv).toEqual({DOCKER_HOST: "unix:///initial.sock"});
        expect(initialization.snapshotOrder).toBeLessThan(initialization.scrubOrder);
        expect(initialization.scrubArgs).toEqual([{DOCS_BASE_URL: BASE_URL}]);
    });
});

describe("local capture", () => {
    it("filters flags, runs selected walkthroughs, and reports shot states", async () => {
        const walkthroughs = [{page: "library/browsing"}];
        loadWalkthroughs.mockResolvedValue(walkthroughs);
        runWalkthroughs.mockResolvedValue([result({
            observation: observed("unavailable"),
            shots: [
                {
                    target: "library/assets/browsing/new.webp",
                    width: 100,
                    height: 80,
                    status: "new",
                    ignore: [],
                    staged: "/staging/new.webp",
                },
                {
                    target: "library/assets/browsing/changed.webp",
                    width: 200,
                    height: 150,
                    status: "changed",
                    ignore: [],
                    staged: "/staging/changed.webp",
                    diff: {status: "changed", diffPixels: 321},
                },
                {
                    target: "library/assets/browsing/reframed.webp",
                    width: 300,
                    height: 220,
                    status: "changed",
                    ignore: [],
                    staged: "/staging/reframed.webp",
                    diff: {status: "changed", diffPixels: null, reason: "size"},
                },
                {
                    target: "library/assets/browsing/same.webp",
                    width: 400,
                    height: 300,
                    status: "unchanged",
                    ignore: [],
                    staged: "/staging/same.webp",
                },
            ],
        })]);

        await expect(main(["--unknown", "library/browsing"], {})).resolves.toBe(0);

        expect(loadWalkthroughs).toHaveBeenCalledWith(CONTENT_DIR, ["library/browsing"]);
        expect(runWalkthroughs).toHaveBeenCalledWith(walkthroughs, {
            baseUrl: BASE_URL,
            guideDir: GUIDE_DIR,
            stagingDir: CAPTURE_DIR,
            promote: false,
        });
        expect(log).toHaveBeenCalledWith(expect.stringContaining("~ library/assets/browsing/changed.webp"));
        expect(log).toHaveBeenCalledWith(expect.stringContaining("321 px differ"));
        expect(log).toHaveBeenCalledWith(expect.stringContaining("reframed"));
        expect(log).toHaveBeenCalledWith(expect.stringContaining("server observer unavailable"));
        expect(log).toHaveBeenCalledWith("\n3 shot(s) new or changed.");
        expect(error).not.toHaveBeenCalled();
    });

    it.each([
        [[], "No walkthroughs found"],
        [["library/missing"], "No walkthrough for: library/missing"],
    ])("returns failure when selection %j finds nothing", async (args, message) => {
        loadWalkthroughs.mockResolvedValue([]);
        await expect(main(args, {})).resolves.toBe(1);
        expect(error).toHaveBeenCalledWith(message);
        expect(runWalkthroughs).not.toHaveBeenCalled();
    });

    it("returns failure after reporting every walkthrough error", async () => {
        loadWalkthroughs.mockResolvedValue([{page: "one"}, {page: "two"}]);
        runWalkthroughs.mockResolvedValue([
            result({page: "one", error: "first failed"}),
            result({page: "two", error: "second failed"}),
        ]);

        await expect(main([], {})).resolves.toBe(1);
        expect(error).toHaveBeenCalledWith("  ! first failed");
        expect(error).toHaveBeenCalledWith("  ! second failed");
    });

    it("writes promoted lockfiles with geometry, observations, and committed hashes", async () => {
        const pageDir = join(CONTENT_DIR, "library", "browsing");
        mkdirSync(pageDir, {recursive: true});
        const committedTarget = "library/assets/browsing/committed.webp";
        const missingTarget = "library/assets/browsing/missing.webp";
        const committedBytes = "committed image bytes";
        write(join(GUIDE_DIR, committedTarget), committedBytes);
        loadWalkthroughs.mockResolvedValue([{page: "library/browsing"}]);
        runWalkthroughs.mockResolvedValue([result({
            shots: [
                {
                    target: committedTarget, width: 640, height: 480,
                    status: "changed", ignore: [], staged: "/staged/committed.webp",
                },
                {
                    target: missingTarget, width: 320, height: 200,
                    status: "new", ignore: [], staged: "/staged/missing.webp",
                },
            ],
        })]);

        await expect(main(["--promote", "library/browsing"], {})).resolves.toBe(0);

        const lock = JSON.parse(readFileSync(join(pageDir, "browsing.lock.json"), "utf8"));
        expect(lock).toEqual({
            page: "library/browsing",
            lastVerifiedSha: null,
            observed: observed(),
            shots: {
                [committedTarget]: {
                    width: 640,
                    height: 480,
                    sha256: createHash("sha256").update(committedBytes).digest("hex"),
                },
                [missingTarget]: {width: 320, height: 200, sha256: null},
            },
        });
        expect(log).toHaveBeenCalledWith("\n2 shot(s) new or changed and promoted.");
    });

    it("skips a promoted lockfile when the walkthrough directory is absent", async () => {
        loadWalkthroughs.mockResolvedValue([{page: "other/missing"}]);
        runWalkthroughs.mockResolvedValue([result({page: "other/missing"})]);
        await expect(main(["--promote"], {})).resolves.toBe(0);
        expect(error).not.toHaveBeenCalled();
    });
});

describe("Docker capture", () => {
    const dockerEnv = {DOCKER_HOST: "unix:///daemon.sock"};

    it("fails clearly before launching when Docker is unreachable", async () => {
        dockerAvailable.mockReturnValue(false);
        await expect(main(["--docker"], dockerEnv)).resolves.toBe(1);
        expect(dockerAvailable).toHaveBeenCalledWith(dockerEnv);
        expect(runCaptureContainer).not.toHaveBeenCalled();
        expect(error).toHaveBeenCalledWith(expect.stringContaining("Docker is not reachable"));
    });

    it("returns the container's nonzero status without processing partial output", async () => {
        runCaptureContainer.mockReturnValue(17);
        await expect(main(["--docker", "library/browsing"], dockerEnv)).resolves.toBe(17);
        expect(runCaptureContainer).toHaveBeenCalledWith({
            repoDir: HOST_REPO,
            stagingDir: CAPTURE_DIR,
            baseUrl: BASE_URL,
            pages: ["library/browsing"],
            dockerEnv,
        });
        expect(processResults).not.toHaveBeenCalled();
        expect(loadWalkthroughs).not.toHaveBeenCalled();
    });

    it("requires the container to leave raw capture results", async () => {
        await expect(main(["--docker"], dockerEnv)).resolves.toBe(1);
        expect(error).toHaveBeenCalledWith("The container produced no raw.json.");
        expect(processResults).not.toHaveBeenCalled();
    });

    it("processes the shared raw file on the host without importing walkthroughs", async () => {
        const rawResults = [{page: "library/browsing", shots: [], observation: observed()}];
        write(join(CAPTURE_DIR, "raw.json"), JSON.stringify({results: rawResults}));
        processResults.mockReturnValue([result()]);

        await expect(main(["--docker", "--promote", "library/browsing"], dockerEnv))
            .resolves.toBe(0);

        expect(processResults).toHaveBeenCalledWith(rawResults, {
            guideDir: GUIDE_DIR,
            stagingDir: CAPTURE_DIR,
            promote: true,
        });
        expect(loadWalkthroughs).not.toHaveBeenCalled();
        expect(log).toHaveBeenCalledWith(expect.stringContaining("yaffo-docs-capture:latest"));
    });

    it("lets malformed container output reach the direct-run error handler", async () => {
        write(join(CAPTURE_DIR, "raw.json"), "not JSON");
        await expect(main(["--docker"], dockerEnv)).rejects.toThrow();
    });
});

describe("direct-run wrapper", () => {
    it("assigns the orchestration return code to the process", async () => {
        loadWalkthroughs.mockResolvedValue([]);
        await runCli([]);
        expect(process.exitCode).toBe(1);
    });

    it("reports a rejected run and assigns failure", async () => {
        const failure = new Error("walkthrough loader failed");
        loadWalkthroughs.mockRejectedValue(failure);
        await runCli([]);
        expect(error).toHaveBeenCalledWith(failure);
        expect(process.exitCode).toBe(1);
    });
});
