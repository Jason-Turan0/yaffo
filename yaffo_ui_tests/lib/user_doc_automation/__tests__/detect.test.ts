import {afterEach, beforeEach, describe, expect, it, jest} from "@jest/globals";
import {mkdirSync, mkdtempSync, rmSync, writeFileSync} from "fs";
import {tmpdir} from "os";
import {dirname, join} from "path";
import {detect, main, runCli} from "../detect";
import type {DetectionOptions} from "../detect";
import type {StringChange} from "../strings";

let root: string;
let contentDir: string;
let guideDir: string;
let log: jest.SpiedFunction<typeof console.log>;
let error: jest.SpiedFunction<typeof console.error>;
const savedExitCode = process.exitCode;

const write = (path: string, content: string): void => {
    mkdirSync(dirname(path), {recursive: true});
    writeFileSync(path, content, "utf8");
};

const spec = (...pages: string[]): void => {
    write(join(contentDir, "spec.yaml"), [
        "version: 1",
        "pages:",
        ...pages.map((page) => `  ${page}: {}`),
        "",
    ].join("\n"));
};

const markdown = (page: string, text: string): void => {
    write(join(guideDir, `${page}.md`), text);
};

const lock = (page: string, lastVerifiedSha: string | null): void => {
    const [area, name] = page.split("/");
    write(join(contentDir, area, name, `${name}.lock.json`), JSON.stringify({lastVerifiedSha}));
};

beforeEach(() => {
    root = mkdtempSync(join(tmpdir(), "yaffo-detect-"));
    contentDir = join(root, "content");
    guideDir = join(root, "guide");
    mkdirSync(contentDir, {recursive: true});
    mkdirSync(guideDir, {recursive: true});
    log = jest.spyOn(console, "log").mockImplementation(() => undefined);
    error = jest.spyOn(console, "error").mockImplementation(() => undefined);
});

afterEach(() => {
    process.exitCode = savedExitCode;
    log.mockRestore();
    error.mockRestore();
    rmSync(root, {recursive: true, force: true});
});

const options = (
    findChanges: (base: string) => StringChange[],
    over: Partial<DetectionOptions> = {}
): DetectionOptions => ({contentDir, guideDir, changedStrings: findChanges, ...over});

describe("detect", () => {
    it("caches catalogue diffs per watermark and flags only quoted changes", () => {
        spec("area/one", "area/two", "area/three", "area/four");
        lock("area/one", "sha-a");
        lock("area/two", "sha-a");
        lock("area/three", null);
        lock("area/four", "sha-b");
        markdown("area/one", "Click **Apply Filters** now.");
        markdown("area/two", "Apply Filters appear here only as ordinary prose.");
        // area/four deliberately has no Markdown page.
        const apply: StringChange = {
            was: "Apply Filters",
            now: "Apply",
            source: "en.json",
            key: "filters.apply",
        };
        const findChanges = jest.fn((base: string): StringChange[] =>
            base === "sha-a" ? [apply] : [{was: "Save", source: "messages.pot"}]);

        expect(detect(options(findChanges))).toEqual({
            flagged: [{page: "area/one", changes: [apply]}],
            scanned: 3,
            unwatermarked: 1,
        });
        expect(findChanges).toHaveBeenCalledTimes(2);
        expect(findChanges).toHaveBeenCalledWith("sha-a");
        expect(findChanges).toHaveBeenCalledWith("sha-b");
    });

    it("uses a base override for every page, including pages without lockfiles", () => {
        spec("area/one", "area/two");
        markdown("area/one", "Press `Save`.");
        markdown("area/two", "Press **Save**.");
        const change: StringChange = {was: "Save", source: "messages.pot"};
        const findChanges = jest.fn<(base: string) => StringChange[]>(() => [change]);

        expect(detect(options(findChanges, {overrideBase: "explicit-sha"}))).toEqual({
            flagged: [
                {page: "area/one", changes: [change]},
                {page: "area/two", changes: [change]},
            ],
            scanned: 2,
            unwatermarked: 0,
        });
        expect(findChanges).toHaveBeenCalledTimes(1);
        expect(findChanges).toHaveBeenCalledWith("explicit-sha");
    });

    it("skips Markdown reads when a watermark has no catalogue changes", () => {
        spec("area/missing-page");
        lock("area/missing-page", "clean-sha");
        expect(detect(options(() => []))).toEqual({
            flagged: [], scanned: 1, unwatermarked: 0,
        });
    });

    it("counts absent lockfiles and null watermarks as unscannable", () => {
        spec("area/no-lock", "area/null-lock");
        lock("area/null-lock", null);
        const findChanges = jest.fn<(base: string) => StringChange[]>(() => []);
        expect(detect(options(findChanges))).toEqual({
            flagged: [], scanned: 0, unwatermarked: 2,
        });
        expect(findChanges).not.toHaveBeenCalled();
    });

    it("treats an omitted pages map as an empty detection set", () => {
        write(join(contentDir, "spec.yaml"), "version: 1\n");
        expect(detect(options(() => []))).toEqual({
            flagged: [], scanned: 0, unwatermarked: 0,
        });
    });
});

describe("detection CLI", () => {
    it("prints a clean summary and skipped-watermark count", () => {
        spec("area/clean", "area/new");
        lock("area/clean", "sha");
        expect(main([], options(() => []))).toBe(0);
        expect(log).toHaveBeenCalledWith(
            "✅ 1 page(s) checked — none quotes a string the app has changed.");
        expect(log).toHaveBeenCalledWith(
            "1 page(s) skipped: no watermark yet (never promoted).");
    });

    it("prints replacement and removal details and returns the stale-docs status", () => {
        spec("area/page");
        lock("area/page", "sha");
        markdown("area/page", "Click **Apply Filters**, then press `Save`.");
        const changes: StringChange[] = [
            {was: "Apply Filters", now: "Apply", source: "en.json", key: "filters.apply"},
            {was: "Save", source: "messages.pot"},
        ];

        expect(main([], options(() => changes))).toBe(2);
        expect(log).toHaveBeenCalledWith("\narea/page");
        expect(log).toHaveBeenCalledWith(
            '   "Apply Filters" is now "Apply"  (filters.apply)');
        expect(log).toHaveBeenCalledWith(
            '   "Save" no longer exists  (messages.pot)');
        expect(log).toHaveBeenCalledWith(
            "\n1 page(s) quote text the app no longer shows.");
    });

    it("parses --base and uses the following argument as the override", () => {
        spec("area/page");
        markdown("area/page", "# Page");
        const findChanges = jest.fn<(base: string) => StringChange[]>(() => []);
        expect(main(["--base", "override-sha"], options(findChanges))).toBe(0);
        expect(findChanges).toHaveBeenCalledWith("override-sha");
    });

    it("assigns status two when stale quoted text is found", () => {
        spec("area/page");
        lock("area/page", "sha");
        markdown("area/page", "Press **Save**.");
        runCli([], options(() => [{was: "Save", source: "messages.pot"}]));
        expect(process.exitCode).toBe(2);
    });

    it("reports malformed lockfiles as infrastructure failures", () => {
        spec("area/page");
        const [area, name] = "area/page".split("/");
        write(join(contentDir, area, name, `${name}.lock.json`), "not JSON");
        runCli([], options(() => []));
        expect(process.exitCode).toBe(1);
        expect(error).toHaveBeenCalledWith(expect.any(SyntaxError));
    });
});
