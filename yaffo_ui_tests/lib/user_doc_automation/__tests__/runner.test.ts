import {afterEach, beforeEach, describe, expect, it, jest} from "@jest/globals";
import {existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync} from "fs";
import {tmpdir} from "os";
import {dirname, join} from "path";
import type {Walkthrough} from "../types";
import type {CompareResult} from "../compare";
import type {Box} from "../framing";

const launch = jest.fn<() => Promise<unknown>>();
const compareShots = jest.fn<(
    baseline: string,
    candidate: string,
    ignore?: Box[],
    diffOut?: string
) => CompareResult>();
const resolveClip = jest.fn<(page: unknown, shot: unknown) => Promise<Box | undefined>>();
const resolveIgnoreRegions = jest.fn<(
    page: unknown,
    shot: unknown,
    clip: Box | undefined
) => Promise<Box[]>>();
const toWebp = jest.fn<(pngPath: string) => string>();
const observerAttach = jest.fn<(context: unknown) => void>();
const observerResult = jest.fn<(server?: unknown) => unknown>();
const createObserver = jest.fn<(page: string, baseUrl: string) => unknown>();
const takeServerObservation = jest.fn<(baseUrl: string, runId: string) => Promise<unknown>>();
const mediaIdByFilename = jest.fn<(baseUrl: string, filename: string) => Promise<number>>();
const blockOsSideEffects = jest.fn<(context: unknown) => Promise<void>>();
const settle = jest.fn<(page: unknown) => Promise<void>>();

await jest.unstable_mockModule("@playwright/test", () => ({
    chromium: {launch},
}));
await jest.unstable_mockModule("../compare", () => ({compareShots}));
await jest.unstable_mockModule("../framing", () => ({resolveClip, resolveIgnoreRegions}));
await jest.unstable_mockModule("../encode", () => ({toWebp}));
await jest.unstable_mockModule("../observe", () => ({
    createObserver,
    PAGE_HEADER: "X-Yaffo-Doc-Page",
    RUN_HEADER: "X-Yaffo-Doc-Run",
    takeServerObservation,
}));
await jest.unstable_mockModule("../media_lookup", () => ({mediaIdByFilename}));
await jest.unstable_mockModule("../side_effects", () => ({blockOsSideEffects}));
await jest.unstable_mockModule("../settle", () => ({settle}));

const {
    captureWalkthroughs,
    DEVICE_SCALE_FACTOR,
    processResults,
    RAW_FILENAME,
    runWalkthroughs,
} = await import("../runner");
type RawResult = import("../runner").RawResult;

const observation = {
    page: "library/browsing",
    urls: ["/"],
    static: ["yaffo/static/js/gallery.js"],
    templates: ["yaffo/templates/home.html"],
    routes: ["yaffo/routes/home.py"],
    serverObserver: "recorded" as const,
};

let testDir: string;
let stagingDir: string;
let guideDir: string;

const pageFake = () => ({
    setViewportSize: jest.fn<(size: {width: number; height: number}) => Promise<void>>(
        async () => undefined),
    goto: jest.fn<(url: string, options?: unknown) => Promise<void>>(async () => undefined),
    screenshot: jest.fn<(options: unknown) => Promise<void>>(async () => undefined),
    close: jest.fn<() => Promise<void>>(async () => undefined),
});

const browserFake = (...pages: ReturnType<typeof pageFake>[]) => {
    const context = {
        newPage: jest.fn<() => Promise<ReturnType<typeof pageFake>>>(),
        close: jest.fn<() => Promise<void>>(async () => undefined),
    };
    for (const page of pages) context.newPage.mockResolvedValueOnce(page);
    const browser = {
        newContext: jest.fn<(options: unknown) => Promise<typeof context>>(async () => context),
        close: jest.fn<() => Promise<void>>(async () => undefined),
    };
    return {browser, context};
};

beforeEach(() => {
    testDir = mkdtempSync(join(tmpdir(), "yaffo-runner-"));
    stagingDir = join(testDir, "staging");
    guideDir = join(testDir, "guide");
    mkdirSync(stagingDir, {recursive: true});
    mkdirSync(guideDir, {recursive: true});

    for (const mock of [
        launch, compareShots, resolveClip, resolveIgnoreRegions, toWebp,
        observerAttach, observerResult, createObserver, takeServerObservation,
        mediaIdByFilename, blockOsSideEffects, settle,
    ]) mock.mockReset();

    createObserver.mockReturnValue({attach: observerAttach, result: observerResult});
    observerResult.mockReturnValue(observation);
    takeServerObservation.mockResolvedValue({
        routes: observation.routes,
        templates: observation.templates,
        serverObserver: "recorded",
    });
    mediaIdByFilename.mockResolvedValue(42);
    blockOsSideEffects.mockResolvedValue(undefined);
    settle.mockResolvedValue(undefined);
    resolveClip.mockResolvedValue(undefined);
    resolveIgnoreRegions.mockResolvedValue([]);
    toWebp.mockImplementation((pngPath) => {
        const webpPath = pngPath.replace(/\.png$/, ".webp");
        mkdirSync(dirname(webpPath), {recursive: true});
        writeFileSync(webpPath, `encoded:${pngPath}`, "utf8");
        return webpPath;
    });
});

afterEach(() => {
    rmSync(testDir, {recursive: true, force: true});
});

describe("captureWalkthroughs", () => {
    it("captures shots and flows with deterministic browser settings", async () => {
        const clippedPage = pageFake();
        const viewportPage = pageFake();
        const flowPage = pageFake();
        const {browser, context} = browserFake(clippedPage, viewportPage, flowPage);
        launch.mockResolvedValue(browser);
        resolveClip
            .mockResolvedValueOnce({x: 10, y: 20, width: 500.4, height: 240.6})
            .mockResolvedValueOnce(undefined);
        resolveIgnoreRegions
            .mockResolvedValueOnce([{x: 3, y: 4, width: 20, height: 10}])
            .mockResolvedValueOnce([]);
        const setup = jest.fn<(page: unknown) => Promise<void>>(async () => undefined);
        const dynamicGoto = jest.fn(async ({mediaIdByFilename: lookup}: {
            mediaIdByFilename: (filename: string) => Promise<number>;
        }) => `/media/view/${await lookup("stable-photo.jpg")}`);
        const flows = jest.fn(async ({visit, mediaIdByFilename: lookup}: {
            visit: (path: string) => Promise<void>;
            mediaIdByFilename: (filename: string) => Promise<number>;
        }) => {
            const id = await lookup("flow-photo.jpg");
            await visit(`/media/view/${id}/edit`);
        });
        const walkthrough: Walkthrough = {
            page: "library/browsing",
            shots: {
                "clipped.webp": {
                    viewport: {width: 1400, height: 900},
                    goto: "/?page=2",
                    clip: "#gallery",
                    setup: setup as Walkthrough["shots"][string]["setup"],
                },
                "detail.webp": {
                    viewport: {width: 1000, height: 700},
                    goto: dynamicGoto as Walkthrough["shots"][string]["goto"],
                },
            },
            flows: flows as Walkthrough["flows"],
        };
        writeFileSync(join(stagingDir, "stale.txt"), "old capture", "utf8");

        const captured = await captureWalkthroughs([walkthrough], {
            baseUrl: "http://app.test:5002",
            stagingDir,
        });

        expect(existsSync(join(stagingDir, "stale.txt"))).toBe(false);
        expect(browser.newContext).toHaveBeenCalledWith(expect.objectContaining({
            baseURL: "http://app.test:5002",
            deviceScaleFactor: DEVICE_SCALE_FACTOR,
            locale: "en-US",
            timezoneId: "America/Chicago",
            colorScheme: "light",
            reducedMotion: "reduce",
            extraHTTPHeaders: {
                "X-Yaffo-Doc-Page": "library/browsing",
                "X-Yaffo-Doc-Run": expect.any(String),
            },
        }));
        expect(createObserver).toHaveBeenCalledWith("library/browsing", "http://app.test:5002");
        expect(observerAttach).toHaveBeenCalledWith(context);
        expect(blockOsSideEffects).toHaveBeenCalledWith(context);
        expect(clippedPage.goto).toHaveBeenCalledWith(
            "http://app.test:5002/?page=2", {waitUntil: "domcontentloaded"});
        expect(viewportPage.goto).toHaveBeenCalledWith(
            "http://app.test:5002/media/view/42", {waitUntil: "domcontentloaded"});
        expect(flowPage.goto).toHaveBeenCalledWith(
            "http://app.test:5002/media/view/42/edit", {waitUntil: "domcontentloaded"});
        expect(mediaIdByFilename).toHaveBeenCalledWith(
            "http://app.test:5002", "stable-photo.jpg");
        expect(mediaIdByFilename).toHaveBeenCalledWith(
            "http://app.test:5002", "flow-photo.jpg");
        expect(setup).toHaveBeenCalledWith(clippedPage);
        expect(settle).toHaveBeenCalledTimes(4);
        expect(clippedPage.screenshot).toHaveBeenCalledWith({
            path: join(stagingDir, "library", "assets", "browsing", "clipped.png"),
            clip: {x: 10, y: 20, width: 500.4, height: 240.6},
            scale: "device",
        });
        expect(viewportPage.screenshot).toHaveBeenCalledWith({
            path: join(stagingDir, "library", "assets", "browsing", "detail.png"),
            clip: undefined,
            scale: "device",
        });
        expect(captured).toEqual([{
            page: "library/browsing",
            shots: [
                {
                    target: "library/assets/browsing/clipped.webp",
                    width: 500,
                    height: 241,
                    ignore: [{x: 3, y: 4, width: 20, height: 10}],
                },
                {
                    target: "library/assets/browsing/detail.webp",
                    width: 1000,
                    height: 700,
                    ignore: [],
                },
            ],
            observation,
            error: undefined,
        }]);
        expect(clippedPage.close).toHaveBeenCalledTimes(1);
        expect(viewportPage.close).toHaveBeenCalledTimes(1);
        expect(flowPage.close).toHaveBeenCalledTimes(1);
        expect(context.close).toHaveBeenCalledTimes(1);
        expect(browser.close).toHaveBeenCalledTimes(1);
        expect(takeServerObservation).toHaveBeenCalledWith(
            "http://app.test:5002", expect.any(String));

        const raw = JSON.parse(readFileSync(join(stagingDir, RAW_FILENAME), "utf8"));
        expect(raw.capturedAt).toEqual(expect.any(String));
        expect(raw.results).toEqual(captured);
    });

    it("records a walkthrough failure while still closing resources and observing the server", async () => {
        const page = pageFake();
        const {browser, context} = browserFake(page);
        launch.mockResolvedValue(browser);
        const setup = jest.fn(async () => { throw "fixture was not ready"; });
        const walkthrough: Walkthrough = {
            page: "library/broken",
            shots: {
                "broken.webp": {
                    viewport: {width: 800, height: 600},
                    goto: "/broken",
                    setup,
                },
            },
        };

        const [captured] = await captureWalkthroughs([walkthrough], {
            baseUrl: "http://app.test",
            stagingDir,
        });

        expect(captured.error).toBe("fixture was not ready");
        expect(captured.shots).toEqual([]);
        expect(page.screenshot).not.toHaveBeenCalled();
        expect(page.close).toHaveBeenCalledTimes(1);
        expect(context.close).toHaveBeenCalledTimes(1);
        expect(takeServerObservation).toHaveBeenCalledTimes(1);
        expect(observerResult).toHaveBeenCalledTimes(1);
        expect(browser.close).toHaveBeenCalledTimes(1);
    });

    it("closes the browser when context creation fails", async () => {
        const {browser} = browserFake();
        browser.newContext.mockRejectedValue(new Error("Chromium context failed"));
        launch.mockResolvedValue(browser);
        const walkthrough: Walkthrough = {
            page: "library/broken",
            shots: {},
        };

        await expect(captureWalkthroughs([walkthrough], {
            baseUrl: "http://app.test", stagingDir,
        })).rejects.toThrow("Chromium context failed");
        expect(browser.close).toHaveBeenCalledTimes(1);
    });
});

describe("processResults", () => {
    const raw = (over: Partial<RawResult> = {}): RawResult => ({
        page: "library/browsing",
        shots: [{
            target: "library/assets/browsing/gallery.webp",
            width: 1400,
            height: 800,
            ignore: [{x: 10, y: 20, width: 30, height: 40}],
        }],
        observation,
        ...over,
    });

    it("encodes a new capture and writes the machine-readable report", () => {
        const [processed] = processResults([raw()], {guideDir, stagingDir});
        const [shot] = processed.shots;

        expect(toWebp).toHaveBeenCalledWith(
            join(stagingDir, "library", "assets", "browsing", "gallery.png"));
        expect(compareShots).not.toHaveBeenCalled();
        expect(shot).toMatchObject({
            target: "library/assets/browsing/gallery.webp",
            staged: join(stagingDir, "library", "assets", "browsing", "gallery.webp"),
            status: "new",
            diff: undefined,
        });
        const report = JSON.parse(readFileSync(join(stagingDir, "report.json"), "utf8"));
        expect(report.generatedAt).toEqual(expect.any(String));
        expect(report.results).toEqual([processed]);
    });

    it("compares an existing capture and leaves an unchanged baseline alone", () => {
        const target = "library/assets/browsing/gallery.webp";
        const committed = join(guideDir, target);
        mkdirSync(dirname(committed), {recursive: true});
        writeFileSync(committed, "committed baseline", "utf8");
        compareShots.mockReturnValue({status: "unchanged", diffPixels: 0, box: null});

        const [processed] = processResults([raw()], {guideDir, stagingDir, promote: true});
        const staged = join(stagingDir, target);

        expect(compareShots).toHaveBeenCalledWith(
            committed,
            staged,
            [{x: 10, y: 20, width: 30, height: 40}],
            staged.replace(/\.webp$/, ".diff.png")
        );
        expect(processed.shots[0]).toMatchObject({
            status: "unchanged",
            diff: {status: "unchanged", diffPixels: 0},
        });
        expect(readFileSync(committed, "utf8")).toBe("committed baseline");
    });

    it("promotes a changed candidate only when requested", () => {
        const target = "library/assets/browsing/gallery.webp";
        const committed = join(guideDir, target);
        mkdirSync(dirname(committed), {recursive: true});
        writeFileSync(committed, "old baseline", "utf8");
        compareShots.mockReturnValue({
            status: "changed",
            diffPixels: 200,
            ratio: 0.01,
            box: {x: 1, y: 2, width: 3, height: 4},
        });

        processResults([raw()], {guideDir, stagingDir, promote: false});
        expect(readFileSync(committed, "utf8")).toBe("old baseline");

        const [promoted] = processResults([raw()], {guideDir, stagingDir, promote: true});
        expect(promoted.shots[0].status).toBe("changed");
        expect(readFileSync(committed, "utf8")).toContain("encoded:");
    });

    it("promotes a new candidate into a newly created guide directory", () => {
        const target = "new-area/assets/new-page/new.webp";
        const [processed] = processResults([raw({
            page: "new-area/new-page",
            shots: [{target, width: 600, height: 400, ignore: []}],
        })], {guideDir, stagingDir, promote: true});

        expect(processed.shots[0].status).toBe("new");
        expect(readFileSync(join(guideDir, target), "utf8")).toContain("encoded:");
    });
});

describe("runWalkthroughs", () => {
    it("composes capture and processing for an empty walkthrough set", async () => {
        const {browser} = browserFake();
        launch.mockResolvedValue(browser);

        await expect(runWalkthroughs([], {
            baseUrl: "http://app.test",
            stagingDir,
            guideDir,
        })).resolves.toEqual([]);

        expect(browser.close).toHaveBeenCalledTimes(1);
        expect(existsSync(join(stagingDir, RAW_FILENAME))).toBe(true);
        expect(existsSync(join(stagingDir, "report.json"))).toBe(true);
    });
});
