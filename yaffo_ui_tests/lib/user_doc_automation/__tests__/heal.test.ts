import {afterAll, afterEach, beforeEach, describe, expect, it, jest} from "@jest/globals";
import {mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync} from "fs";
import {tmpdir} from "os";
import {dirname, join} from "path";

const root = mkdtempSync(join(tmpdir(), "yaffo-heal-"));
const CONTENT_DIR = join(root, "content");
const GUIDE_DIR = join(root, "guide");
const STAGING_DIR = join(root, "staging");
const CAPTURE_DIR = join(STAGING_DIR, "captures");
const RUN_LOG_DIR = join(STAGING_DIR, "heal-logs", "run");
const BASE_URL = "http://app.test:5002";
const APP_ROOT = join(root, "app");

const applyFix = jest.fn<(...args: unknown[]) => Promise<unknown>>();
const buildEvidence = jest.fn<(...args: unknown[]) => unknown>();
const openSession = jest.fn<(...args: unknown[]) => unknown>();
const triageShot = jest.fn<(...args: unknown[]) => Promise<unknown>>();
const changedStrings = jest.fn<(base: string) => Array<Record<string, unknown>>>();
const changesQuotedBy = jest.fn<(
    markdown: string, changes: Array<Record<string, unknown>>
) => Array<Record<string, unknown>>>();
const changedDependencies = jest.fn<(
    lock: Record<string, unknown>, alsoDependsOn?: string[]
) => Array<Record<string, unknown>>>();
const createFilesystemClient = jest.fn<(...args: unknown[]) => Promise<unknown>>();
const createPlaywrightClient = jest.fn<(...args: unknown[]) => Promise<unknown>>();
const localFilesystemMemoryToolFactory = jest.fn<(pageDir: string) => unknown>();
const newRunLogDir = jest.fn(() => RUN_LOG_DIR);

await jest.unstable_mockModule("../index", () => ({
    applyFix, buildEvidence, openSession, triageShot,
}));
await jest.unstable_mockModule("../strings", () => ({changedStrings, changesQuotedBy}));
await jest.unstable_mockModule("../dependency_changes", () => ({changedDependencies}));
await jest.unstable_mockModule("@lib/tool_providers/mcp_filesystem_client", () => ({
    createFilesystemClient,
}));
await jest.unstable_mockModule("@lib/tool_providers/mcp_playwright_client", () => ({
    createPlaywrightClient,
}));
await jest.unstable_mockModule("@lib/tool_providers/local_filesystem_memory_tool", () => ({
    localFilesystemMemoryToolFactory,
}));
await jest.unstable_mockModule("@lib/types", () => ({YAFFO_APP_ROOT: APP_ROOT}));
await jest.unstable_mockModule("../paths", () => ({
    BASE_URL, CAPTURE_DIR, CONTENT_DIR, GUIDE_DIR, newRunLogDir, STAGING_DIR,
}));

const {
    acceptMinorEnvironmentVariation,
    main,
    MINOR_VARIATION_MAX_RATIO,
    runCli,
} = await import("../heal");

let filesystem: {disconnect: jest.Mock<() => Promise<void>>};
let browser: {disconnect: jest.Mock<() => Promise<void>>};
let memory: {disconnect: jest.Mock<() => Promise<void>>};
let log: jest.SpiedFunction<typeof console.log>;
let error: jest.SpiedFunction<typeof console.error>;
const savedExitCode = process.exitCode;

const write = (path: string, contents: string): void => {
    mkdirSync(dirname(path), {recursive: true});
    writeFileSync(path, contents, "utf8");
};

const writeSpec = (pages = "  area/page: {covers: Page charter}\n"): void => {
    write(join(CONTENT_DIR, "spec.yaml"), `version: 1\npages:\n${pages}`);
};

const observation = {routes: [], templates: [], static: []};
const shot = (over: Record<string, unknown> = {}) => ({
    target: "area/assets/page/gallery.webp",
    staged: join(CAPTURE_DIR, "area", "assets", "page", "gallery.webp"),
    status: "changed",
    width: 100,
    height: 80,
    ignore: [],
    ...over,
});
const result = (over: Record<string, unknown> = {}) => ({
    page: "area/page",
    shots: [shot()],
    observation,
    ...over,
});
const writeReport = (results: unknown[]): void => {
    write(join(CAPTURE_DIR, "report.json"), JSON.stringify({results}));
};

const intended = {
    classification: "intended_change",
    confidence: "high" as const,
    summary: "The app changed intentionally.",
    reasoning: "The new control matches the implementation.",
    proseImpact: [{quote: "Old label", issue: "It is now New label"}],
    recommendedAction: "promote",
};
const environmentInstability = {
    ...intended,
    classification: "environment_instability" as const,
    summary: "Minor renderer variation.",
    reasoning: "Only anti-aliased text edges changed.",
    proseImpact: [],
    recommendedAction: "quarantine" as const,
};
const outcome = {
    fix: {files: [], explanation: "Updated the prose."},
    written: ["docs/guide/area/page.md"],
    attempts: 2,
    failures: [],
    reverted: false,
};

beforeEach(() => {
    rmSync(CONTENT_DIR, {recursive: true, force: true});
    rmSync(GUIDE_DIR, {recursive: true, force: true});
    rmSync(STAGING_DIR, {recursive: true, force: true});
    mkdirSync(CONTENT_DIR, {recursive: true});
    mkdirSync(GUIDE_DIR, {recursive: true});
    mkdirSync(CAPTURE_DIR, {recursive: true});
    writeSpec();
    write(join(GUIDE_DIR, "area", "page.md"), "Click **Old label**.\n");
    write(join(CONTENT_DIR, "area", "page", "page.ts"), "export default {};\n");

    filesystem = {disconnect: jest.fn(async () => undefined)};
    browser = {disconnect: jest.fn(async () => undefined)};
    memory = {disconnect: jest.fn(async () => undefined)};
    for (const mock of [
        applyFix, buildEvidence, openSession, triageShot, changedStrings, changesQuotedBy,
        changedDependencies, createFilesystemClient, createPlaywrightClient,
        localFilesystemMemoryToolFactory,
        newRunLogDir,
    ]) mock.mockReset();
    buildEvidence.mockImplementation((rawResult, rawShot, options) => ({
        page: (rawResult as {page: string}).page,
        target: (rawShot as {target: string}).target,
        ...(options as object),
    }));
    triageShot.mockResolvedValue({triage: intended, client: {}, model: "vision-model"});
    applyFix.mockResolvedValue(outcome);
    openSession.mockReturnValue({client: {}, model: "vision-model"});
    changedStrings.mockReturnValue([]);
    changesQuotedBy.mockReturnValue([]);
    changedDependencies.mockReturnValue([]);
    createFilesystemClient.mockResolvedValue(filesystem);
    createPlaywrightClient.mockResolvedValue(browser);
    localFilesystemMemoryToolFactory.mockReturnValue(memory);
    newRunLogDir.mockReturnValue(RUN_LOG_DIR);
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

describe("minor environment variation policy", () => {
    const diff = (over: Record<string, unknown> = {}) => shot({
        diff: {
            status: "changed",
            diffPixels: 80,
            ratio: MINOR_VARIATION_MAX_RATIO,
            box: {x: 10, y: 10, width: 20, height: 8},
            ...over,
        },
    });

    it("promotes a semantic-free variation at the pixel limit", () => {
        const accepted = acceptMinorEnvironmentVariation(
            environmentInstability, diff() as never);

        expect(accepted.recommendedAction).toBe("promote");
        expect(accepted.classification).toBe("environment_instability");
        expect(accepted.reasoning).toContain("within the 0.1% limit");
    });

    it("keeps material, reframed, and prose-affecting variations quarantined", () => {
        expect(acceptMinorEnvironmentVariation(
            environmentInstability,
            diff({ratio: MINOR_VARIATION_MAX_RATIO + 0.000001}) as never
        ).recommendedAction).toBe("quarantine");
        expect(acceptMinorEnvironmentVariation(
            environmentInstability,
            diff({reason: "size"}) as never
        ).recommendedAction).toBe("quarantine");
        expect(acceptMinorEnvironmentVariation(
            {...environmentInstability, proseImpact: [{quote: "Old", issue: "Changed"}]},
            diff() as never
        ).recommendedAction).toBe("quarantine");
        expect(acceptMinorEnvironmentVariation(
            {...environmentInstability, recommendedAction: "report_regression"},
            diff() as never
        ).recommendedAction).toBe("report_regression");
    });
});

describe("heal CLI", () => {
    it("reports a missing capture without terminating the test process", async () => {
        await expect(main([])).resolves.toBe(1);
        expect(error).toHaveBeenCalledWith(expect.stringContaining("Run: npm run docs:capture"));
        expect(triageShot).not.toHaveBeenCalled();
    });

    it("requires a value after --page", async () => {
        await expect(main(["--page"])).resolves.toBe(1);
        expect(error).toHaveBeenCalledWith("--page requires a page id");
    });

    it("returns cleanly when every capture is unchanged", async () => {
        writeReport([result({shots: [shot({status: "unchanged"})]})]);

        await expect(main([])).resolves.toBe(0);

        expect(log).toHaveBeenCalledWith("Nothing to triage — every shot matched what is committed.");
        expect(newRunLogDir).not.toHaveBeenCalled();
    });

    it("triages a changed shot in dry-run mode and records its verdict", async () => {
        writeReport([result()]);

        await expect(main(["--model", "vision-model"])).resolves.toBe(0);

        expect(buildEvidence).toHaveBeenCalledWith(expect.objectContaining({page: "area/page"}),
            expect.objectContaining({status: "changed"}), expect.objectContaining({
                guideDir: GUIDE_DIR,
                stagingDir: CAPTURE_DIR,
                covers: "Page charter",
                stringChanges: [],
            }));
        expect(triageShot).toHaveBeenCalledWith(expect.anything(), {
            model: "vision-model", runLogDir: RUN_LOG_DIR, toolProviders: [],
        });
        expect(createFilesystemClient).not.toHaveBeenCalled();
        expect(log).toHaveBeenCalledWith(
            "   → would promote and update the page (re-run with --apply)");
        const saved = JSON.parse(readFileSync(join(STAGING_DIR, "triage.json"), "utf8"));
        expect(saved.verdicts).toEqual([{page: "area/page", target: shot().target, triage: intended}]);
    });

    it("returns 2 when a verdict requires human attention", async () => {
        writeReport([result()]);
        triageShot.mockResolvedValue({
            triage: {
                ...intended,
                classification: "application_regression",
                recommendedAction: "report_regression",
            },
        });

        await expect(main([])).resolves.toBe(2);

        expect(log).toHaveBeenCalledWith(
            "   → report_regression: left for a human, nothing written");
    });

    it("accepts a minor environment variation without requiring a human", async () => {
        writeReport([result({shots: [shot({
            diff: {
                status: "changed",
                diffPixels: 40,
                ratio: 0.0005,
                box: {x: 1, y: 2, width: 3, height: 4},
            },
        })]})]);
        triageShot.mockResolvedValue({triage: environmentInstability});

        await expect(main([])).resolves.toBe(0);

        expect(log).toHaveBeenCalledWith(
            "   → would promote and update the page (re-run with --apply)");
        const saved = JSON.parse(readFileSync(join(STAGING_DIR, "triage.json"), "utf8"));
        expect(saved.verdicts[0].triage).toMatchObject({
            classification: "environment_instability",
            recommendedAction: "promote",
        });
    });

    it("returns failure when a changed shot cannot be triaged", async () => {
        writeReport([result()]);
        triageShot.mockRejectedValue(new Error("model unavailable"));

        await expect(main([])).resolves.toBe(1);

        expect(error).toHaveBeenCalledWith(expect.stringContaining("model unavailable"));
        const saved = JSON.parse(readFileSync(join(STAGING_DIR, "triage.json"), "utf8"));
        expect(saved.verdicts).toEqual([]);
    });

    it("promotes and fixes intended changes under --apply, then disconnects providers", async () => {
        const changed = shot();
        write(changed.staged as string, "new image");
        writeReport([result({shots: [changed]})]);

        await expect(main(["--apply", "--docker", "--model", "vision-model"])).resolves.toBe(0);

        expect(createFilesystemClient).toHaveBeenCalledWith(
            [APP_ROOT, GUIDE_DIR, CONTENT_DIR], {readonly: false});
        expect(createPlaywrightClient).toHaveBeenCalledWith(expect.objectContaining({
            baseUrl: BASE_URL,
            artifacts: expect.objectContaining({outputDir: RUN_LOG_DIR}),
        }));
        expect(localFilesystemMemoryToolFactory).toHaveBeenCalledWith(
            join(CONTENT_DIR, "area", "page"));
        expect(applyFix).toHaveBeenCalledWith(expect.objectContaining({triage: intended}),
            expect.anything(), expect.objectContaining({baseUrl: BASE_URL, useDocker: true}));
        expect(readFileSync(join(GUIDE_DIR, changed.target as string), "utf8")).toBe("new image");
        expect(filesystem.disconnect).toHaveBeenCalledTimes(1);
        expect(browser.disconnect).toHaveBeenCalledTimes(1);
    });

    it("limits capture results and dependency checks to --page", async () => {
        writeSpec([
            "  area/page: {covers: First}",
            "  area/other: {covers: Second}",
            "",
        ].join("\n"));
        for (const page of ["page", "other"]) {
            write(join(CONTENT_DIR, "area", page, `${page}.lock.json`), "{}");
        }
        writeReport([result(), result({page: "area/other"})]);

        await expect(main(["--page", "area/page"])).resolves.toBe(0);

        expect(triageShot).toHaveBeenCalledTimes(1);
        expect(buildEvidence).toHaveBeenCalledWith(
            expect.objectContaining({page: "area/page"}), expect.anything(), expect.anything());
        expect(changedDependencies).toHaveBeenCalledTimes(1);
    });

    it("caches catalogue diffs by watermark and handles prose-only pages", async () => {
        writeSpec([
            "  area/page: {covers: First}",
            "  area/other: {covers: Second}",
            "",
        ].join("\n"));
        write(join(GUIDE_DIR, "area", "other.md"), "Click **Old label**.\n");
        for (const page of ["page", "other"]) {
            write(join(CONTENT_DIR, "area", page, `${page}.lock.json`),
                JSON.stringify({lastVerifiedSha: "same-base"}));
        }
        writeReport([]);
        const changes = [{was: "Old label", now: "New label", source: "en.json"}];
        changedStrings.mockReturnValue(changes);
        changesQuotedBy.mockImplementation((markdown) =>
            markdown.includes("Old label") ? changes : []);

        await expect(main([])).resolves.toBe(0);

        expect(changedStrings).toHaveBeenCalledTimes(1);
        expect(changedStrings).toHaveBeenCalledWith("same-base");
        expect(triageShot).not.toHaveBeenCalled();
        expect(log).toHaveBeenCalledWith("✏️  area/page");
        expect(log).toHaveBeenCalledWith("✏️  area/other");
    });

    it("updates prose-only pages under --apply and reports caught fix failures", async () => {
        write(join(CONTENT_DIR, "area", "page", "page.lock.json"),
            JSON.stringify({lastVerifiedSha: "base"}));
        writeReport([]);
        const changes = [{was: "Old label", now: "New label", source: "en.json"}];
        changedStrings.mockReturnValue(changes);
        changesQuotedBy.mockReturnValue(changes);
        applyFix.mockRejectedValue(new Error("gates unavailable"));

        await expect(main(["--apply"])).resolves.toBe(1);

        expect(openSession).toHaveBeenCalledWith(expect.objectContaining({
            page: "area/page",
            diffSummary: expect.stringContaining("No screenshot changed"),
            stringChanges: changes,
        }), expect.objectContaining({toolProviders: [filesystem, browser, memory]}));
        expect(error).toHaveBeenCalledWith(expect.stringContaining("gates unavailable"));
        expect(filesystem.disconnect).toHaveBeenCalledTimes(1);
        expect(browser.disconnect).toHaveBeenCalledTimes(1);
    });

    it("starts a fix turn when only a dependency fingerprint changed", async () => {
        write(join(CONTENT_DIR, "area", "page", "page.lock.json"),
            JSON.stringify({dependencyHashes: {"yaffo/templates/base.html": "old"}}));
        writeReport([]);
        changedDependencies.mockReturnValue([{
            path: "yaffo/templates/base.html", before: "old", after: "new",
        }]);

        await expect(main(["--apply"])).resolves.toBe(0);

        expect(openSession).toHaveBeenCalledWith(expect.objectContaining({
            page: "area/page",
            diffSummary: expect.stringContaining("dependency fingerprints changed"),
            codeDiff: expect.stringContaining("yaffo/templates/base.html"),
            stringChanges: [],
        }), expect.objectContaining({toolProviders: [filesystem, browser, memory]}));
        expect(applyFix).toHaveBeenCalled();
        expect(changedStrings).not.toHaveBeenCalled();
    });

    it("fails the run when correctness gates revert a fix", async () => {
        const changed = shot();
        write(changed.staged as string, "new image");
        writeReport([result({shots: [changed]})]);
        applyFix.mockResolvedValue({
            ...outcome,
            failures: ["capture failed"],
            reverted: true,
        });

        await expect(main(["--apply"])).resolves.toBe(1);
        expect(error).toHaveBeenCalledWith("   → edits reverted; gates failed");
    });

    it("disconnects initialized providers and lets the wrapper report uncaught failures", async () => {
        writeReport([result()]);
        const changed = shot();
        write(changed.staged as string, "new image");
        writeReport([result({shots: [changed]})]);
        applyFix.mockRejectedValue(new Error("fix crashed"));

        await runCli(["--apply"]);

        expect(process.exitCode).toBe(1);
        expect(error).toHaveBeenCalledWith(expect.objectContaining({message: "fix crashed"}));
        expect(filesystem.disconnect).toHaveBeenCalledTimes(1);
        expect(browser.disconnect).toHaveBeenCalledTimes(1);
    });
});
