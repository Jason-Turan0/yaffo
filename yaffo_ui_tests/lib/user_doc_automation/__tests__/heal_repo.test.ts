import {afterEach, beforeEach, describe, expect, it, jest} from "@jest/globals";
import {mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync} from "fs";
import {tmpdir} from "os";
import {dirname, join} from "path";
import {discoverHealPages, main, runCli} from "../heal_repo";

let root: string;
let contentDir: string;
let output: string;
let stdout: jest.SpiedFunction<typeof process.stdout.write>;
let error: jest.SpiedFunction<typeof console.error>;
const savedExitCode = process.exitCode;

const write = (path: string, contents = ""): void => {
    mkdirSync(dirname(path), {recursive: true});
    writeFileSync(path, contents, "utf8");
};

const pageFile = (page: string, suffix: ".ts" | ".lock.json"): string => {
    const [area, name] = page.split("/");
    return join(contentDir, area, name, `${name}${suffix}`);
};

beforeEach(() => {
    root = mkdtempSync(join(tmpdir(), "yaffo-doc-heal-repo-"));
    contentDir = join(root, "content");
    output = join(root, "github-output");
    mkdirSync(contentDir, {recursive: true});
    stdout = jest.spyOn(process.stdout, "write").mockImplementation(() => true);
    error = jest.spyOn(console, "error").mockImplementation(() => undefined);
});

afterEach(() => {
    process.exitCode = savedExitCode;
    stdout.mockRestore();
    error.mockRestore();
    rmSync(root, {recursive: true, force: true});
});

describe("discoverHealPages", () => {
    it("includes only spec pages with both a walkthrough and lockfile", () => {
        write(join(contentDir, "spec.yaml"), [
            "pages:",
            "  area/ready: {walkthrough: true}",
            "  area/walkthrough-only: {walkthrough: true}",
            "  area/lock-only: {walkthrough: true}",
            "  area/neither: {walkthrough: true}",
            "  area/disabled: {walkthrough: false}",
            "",
        ].join("\n"));
        write(pageFile("area/ready", ".ts"));
        write(pageFile("area/ready", ".lock.json"), "{}");
        write(pageFile("area/walkthrough-only", ".ts"));
        write(pageFile("area/lock-only", ".lock.json"), "{}");
        // Even stray files cannot opt a page explicitly disabled by the spec into CI.
        write(pageFile("area/disabled", ".ts"));
        write(pageFile("area/disabled", ".lock.json"), "{}");

        expect(discoverHealPages([], {contentDir})).toEqual({
            include: [{id: "area__ready", page: "area/ready"}],
            skipped: [
                {page: "area/disabled", issues: ["walkthrough disabled by spec.yaml"]},
                {page: "area/lock-only", issues: ["missing walkthrough"]},
                {page: "area/neither", issues: ["missing walkthrough", "missing lockfile"]},
                {page: "area/walkthrough-only", issues: ["missing lockfile"]},
            ],
        });
    });

    it("limits discovery to requested pages and removes duplicates", () => {
        write(join(contentDir, "spec.yaml"), [
            "pages:",
            "  area/one: {walkthrough: true}",
            "  area/two: {walkthrough: true}",
            "",
        ].join("\n"));
        for (const page of ["area/one", "area/two"]) {
            write(pageFile(page, ".ts"));
            write(pageFile(page, ".lock.json"), "{}");
        }

        expect(discoverHealPages(["area/two", "area/two"], {contentDir})).toEqual({
            include: [{id: "area__two", page: "area/two"}],
            skipped: [],
        });
    });
});

describe("heal repo CLI", () => {
    it("prints and exports a GitHub matrix with a has-pages guard", () => {
        write(join(contentDir, "spec.yaml"), "pages:\n  area/page: {walkthrough: true}\n");
        write(pageFile("area/page", ".ts"));
        write(pageFile("area/page", ".lock.json"), "{}");

        expect(main(["--github"], {contentDir, githubOutput: output})).toBe(0);

        const matrix = '{"include":[{"id":"area__page","page":"area/page"}]}';
        expect(stdout).toHaveBeenCalledWith(`${matrix}\n`);
        expect(readFileSync(output, "utf8")).toBe(
            `matrix=${matrix}\nhas_pages=true\n`);
    });

    it("exports an empty matrix and explains skipped requested pages", () => {
        write(join(contentDir, "spec.yaml"), "pages:\n  area/page: {walkthrough: true}\n");

        expect(main(["--github", "area/page"], {contentDir, githubOutput: output})).toBe(0);

        expect(readFileSync(output, "utf8")).toBe(
            'matrix={"include":[]}\nhas_pages=false\n');
        expect(error).toHaveBeenCalledWith(
            "Skipping area/page: missing walkthrough; missing lockfile");
    });

    it("turns malformed specs into an infrastructure failure", () => {
        write(join(contentDir, "spec.yaml"), "pages: [");

        runCli([], {contentDir});

        expect(process.exitCode).toBe(1);
        expect(error).toHaveBeenCalledWith(expect.any(Error));
    });
});
