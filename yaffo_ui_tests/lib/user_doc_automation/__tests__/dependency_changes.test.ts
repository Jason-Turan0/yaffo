import {afterEach, beforeEach, describe, expect, it} from "@jest/globals";
import {createHash} from "crypto";
import {mkdirSync, mkdtempSync, rmSync, unlinkSync, writeFileSync} from "fs";
import {tmpdir} from "os";
import {dirname, join} from "path";
import {
    changedDependencies,
    dependencyHashes,
    pageDependencies,
} from "../dependency_changes";

let repoDir: string;

const write = (path: string, contents: string): void => {
    mkdirSync(dirname(path), {recursive: true});
    writeFileSync(path, contents, "utf8");
};

const sha = (contents: string): string =>
    createHash("sha256").update(contents).digest("hex");

beforeEach(() => {
    repoDir = mkdtempSync(join(tmpdir(), "yaffo-doc-dependencies-"));
});

afterEach(() => {
    rmSync(repoDir, {recursive: true, force: true});
});

describe("pageDependencies", () => {
    it("combines, deduplicates, and sorts observed and declared dependencies", () => {
        expect(pageDependencies({
            routes: ["yaffo/routes/home.py"],
            templates: ["yaffo/templates/base.html"],
            static: ["yaffo/static/app.js", "yaffo/templates/base.html"],
        }, ["pyproject.toml", "yaffo/routes/home.py"])).toEqual([
            "pyproject.toml",
            "yaffo/routes/home.py",
            "yaffo/static/app.js",
            "yaffo/templates/base.html",
        ]);
    });
});

describe("dependencyHashes", () => {
    it("records file content hashes and explicit nulls for missing files", () => {
        write(join(repoDir, "yaffo/routes/home.py"), "route contents");

        expect(dependencyHashes({
            routes: ["yaffo/routes/home.py"],
            templates: ["yaffo/templates/missing.html"],
        }, [], repoDir)).toEqual({
            "yaffo/routes/home.py": sha("route contents"),
            "yaffo/templates/missing.html": null,
        });
    });
});

describe("changedDependencies", () => {
    it("returns only dependencies whose current content differs from the lockfile", () => {
        write(join(repoDir, "same.css"), "same");
        write(join(repoDir, "changed.js"), "after");
        write(join(repoDir, "deleted.py"), "before deletion");
        const lock = {
            observed: {
                static: ["same.css", "changed.js"],
                routes: ["deleted.py"],
            },
            dependencyHashes: {
                "same.css": sha("same"),
                "changed.js": sha("before"),
                "deleted.py": sha("before deletion"),
            },
        };
        unlinkSync(join(repoDir, "deleted.py"));

        expect(changedDependencies(lock, [], repoDir)).toEqual([
            {path: "changed.js", before: sha("before"), after: sha("after")},
            {path: "deleted.py", before: sha("before deletion"), after: null},
        ]);
    });

    it("flags dependencies absent from an older lockfile and includes declared paths", () => {
        write(join(repoDir, "observed.py"), "observed");
        write(join(repoDir, "declared.py"), "declared");

        expect(changedDependencies({
            observed: {routes: ["observed.py"]},
        }, ["declared.py"], repoDir)).toEqual([
            {path: "declared.py", before: undefined, after: sha("declared")},
            {path: "observed.py", before: undefined, after: sha("observed")},
        ]);
    });
});
