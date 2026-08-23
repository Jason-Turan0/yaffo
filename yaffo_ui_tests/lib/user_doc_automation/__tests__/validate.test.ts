import {afterEach, beforeEach, describe, expect, it, jest} from "@jest/globals";
import {mkdirSync, mkdtempSync, rmSync, writeFileSync} from "fs";
import {tmpdir} from "os";
import {dirname, join} from "path";
import {main, runCli, validate} from "../validate";
import type {ValidationOptions} from "../validate";

let root: string;
let repoDir: string;
let guideDir: string;
let contentDir: string;
let options: ValidationOptions;
let log: jest.SpiedFunction<typeof console.log>;
let error: jest.SpiedFunction<typeof console.error>;
const savedExitCode = process.exitCode;

const write = (path: string, content = "content"): void => {
    mkdirSync(dirname(path), {recursive: true});
    writeFileSync(path, content, "utf8");
};

const spec = (pages: string): void => {
    write(join(contentDir, "spec.yaml"), `version: 1\npages:\n${pages}`);
};

const page = (id: string, markdown: string): void => {
    write(join(guideDir, `${id}.md`), markdown);
};

beforeEach(() => {
    root = mkdtempSync(join(tmpdir(), "yaffo-validate-"));
    repoDir = join(root, "repo");
    guideDir = join(repoDir, "docs", "guide");
    contentDir = join(repoDir, "yaffo_ui_tests", "user_doc_automation");
    mkdirSync(guideDir, {recursive: true});
    mkdirSync(contentDir, {recursive: true});
    options = {repoDir, guideDir, contentDir};
    log = jest.spyOn(console, "log").mockImplementation(() => undefined);
    error = jest.spyOn(console, "error").mockImplementation(() => undefined);
});

afterEach(() => {
    process.exitCode = savedExitCode;
    log.mockRestore();
    error.mockRestore();
    rmSync(root, {recursive: true, force: true});
});

describe("validate", () => {
    it("accepts a consistent guide, automation tree, lockfile, and manual dependency", () => {
        spec([
            "  library/browsing:",
            "    walkthrough: true",
            "    also_depends_on:",
            "      - yaffo/manual_dependency.py",
            "",
        ].join("\n"));
        page("library/browsing", "# Browsing\n\n![Gallery](assets/browsing/gallery.webp)");
        write(join(guideDir, "library", "assets", "browsing", "gallery.webp"));
        write(join(contentDir, "library", "browsing", "browsing.ts"));
        write(join(repoDir, "yaffo", "manual_dependency.py"));
        write(join(contentDir, "library", "browsing", "browsing.lock.json"), JSON.stringify({
            observed: {
                routes: ["yaffo/routes/home.py"],
                templates: ["yaffo/templates/home.html"],
                static: ["yaffo/static/app.js"],
            },
        }));

        expect(validate(options)).toEqual([]);
    });

    it("reports pages missing from either side of the spec/guide contract", () => {
        spec([
            "  library/spec-only:",
            "    walkthrough: true",
            "",
        ].join("\n"));
        page("library/guide-only", "# Guide only");

        expect(validate(options)).toEqual(expect.arrayContaining([
            {check: "spec", detail: "library/spec-only is in spec.yaml but has no page"},
            {check: "spec", detail: "library/guide-only.md has no spec.yaml entry"},
        ]));
    });

    it("treats an omitted pages map as empty", () => {
        write(join(contentDir, "spec.yaml"), "version: 1\n");
        page("library/unlisted", "# Unlisted");
        expect(validate(options)).toContainEqual({
            check: "spec",
            detail: "library/unlisted.md has no spec.yaml entry",
        });
    });

    it("reports missing, misplaced, and orphaned images independently", () => {
        spec("  library/browsing:\n    walkthrough: true\n");
        page("library/browsing", [
            "![Missing](assets/browsing/missing.webp)",
            "![Shared](../shared.svg)",
        ].join("\n"));
        write(join(guideDir, "shared.svg"));
        write(join(guideDir, "library", "assets", "browsing", "orphan.png"));

        expect(validate(options)).toEqual(expect.arrayContaining([
            {
                check: "images",
                detail: "library/browsing.md references assets/browsing/missing.webp, which does not exist",
            },
            {
                check: "images",
                detail: "library/browsing.md references ../shared.svg from outside its own assets directory",
            },
            {
                check: "images",
                detail: "library/assets/browsing/orphan.png is referenced by no page",
            },
        ]));
    });

    it("ignores non-image files when checking for orphaned captures", () => {
        spec("  library/browsing:\n    walkthrough: true\n");
        page("library/browsing", "# Browsing");
        write(join(guideDir, "library", "assets", "browsing", "notes.txt"));
        expect(validate(options).filter(({check}) => check === "images")).toEqual([]);
    });

    it("rejects a walkthrough for a page explicitly declared non-observable", () => {
        spec("  reference/uninstalling:\n    walkthrough: false\n");
        page("reference/uninstalling", "# Uninstalling");
        write(join(contentDir, "reference", "uninstalling", "uninstalling.ts"));

        expect(validate(options)).toContainEqual({
            check: "walkthrough",
            detail: "reference/uninstalling is marked walkthrough: false but one exists",
        });
    });

    it("reports missing manual dependencies and declarations already observed at runtime", () => {
        spec([
            "  library/browsing:",
            "    walkthrough: true",
            "    also_depends_on:",
            "      - yaffo/missing.py",
            "      - yaffo/routes/home.py",
            "",
        ].join("\n"));
        page("library/browsing", "# Browsing");
        write(join(repoDir, "yaffo", "routes", "home.py"));
        write(join(contentDir, "library", "browsing", "browsing.lock.json"), JSON.stringify({
            observed: {routes: ["yaffo/routes/home.py"]},
        }));

        expect(validate(options)).toEqual(expect.arrayContaining([
            {
                check: "depends",
                detail: "library/browsing declares yaffo/missing.py, which does not exist",
            },
            {
                check: "depends",
                detail: "library/browsing declares yaffo/routes/home.py, which its walkthrough already observes — delete it",
            },
        ]));
    });

    it("allows an existing manual dependency when no lockfile exists yet", () => {
        spec([
            "  library/browsing:",
            "    also_depends_on:",
            "      - yaffo/manual.py",
            "",
        ].join("\n"));
        page("library/browsing", "# Browsing");
        write(join(repoDir, "yaffo", "manual.py"));
        expect(validate(options)).toEqual([]);
    });

    it("handles a lockfile whose observer recorded no dependency arrays", () => {
        spec([
            "  library/browsing:",
            "    also_depends_on:",
            "      - yaffo/manual.py",
            "",
        ].join("\n"));
        page("library/browsing", "# Browsing");
        write(join(repoDir, "yaffo", "manual.py"));
        write(join(contentDir, "library", "browsing", "browsing.lock.json"), "{}");
        expect(validate(options)).toEqual([]);
    });
});

describe("validation CLI", () => {
    it("prints a successful page/image summary and returns zero", () => {
        spec("  library/browsing:\n    walkthrough: true\n");
        page("library/browsing", "![Gallery](assets/browsing/gallery.webp)");
        write(join(guideDir, "library", "assets", "browsing", "gallery.webp"));

        expect(main(options)).toBe(0);
        expect(log).toHaveBeenCalledWith("✅ 1 pages, 1 images — no problems.");
        expect(error).not.toHaveBeenCalled();
    });

    it("prints each categorized problem and the total", () => {
        spec("  library/missing:\n    walkthrough: true\n");
        expect(main(options)).toBe(1);
        expect(error).toHaveBeenCalledWith(
            "  [spec] library/missing is in spec.yaml but has no page");
        expect(error).toHaveBeenCalledWith("\n1 problem(s).");
    });

    it("assigns the validation result to the process exit code", () => {
        spec("  library/missing:\n    walkthrough: true\n");
        runCli(options);
        expect(process.exitCode).toBe(1);
    });

    it("reports unexpected validation failures without terminating the caller", () => {
        // No spec file: the read error is infrastructure, not a consistency problem.
        runCli(options);
        expect(process.exitCode).toBe(1);
        expect(error).toHaveBeenCalledWith(expect.objectContaining({code: "ENOENT"}));
    });
});
