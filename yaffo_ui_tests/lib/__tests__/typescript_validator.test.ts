import {describe, expect, it} from "@jest/globals";
import {join, resolve} from "path";
import {diagnosticsForFile, findTsconfig, typeCheckFile} from "@lib/services/typescript_validator";

const PROJECT = resolve(process.cwd());
const TARGET = join(PROJECT, "generated_tests", "themes", "themes.spec.ts");

describe("diagnosticsForFile", () => {
    it("keeps only diagnostics whose path resolves to the target file", () => {
        const output = [
            "generated_tests/themes/themes.spec.ts(12,7): error TS2322: Type 'string' is not assignable to type 'number'.",
            "generated_tests/albums/albums.spec.ts(4,1): error TS2304: Cannot find name 'foo'.",
            "lib/services/whatever.ts(9,9): error TS2554: Expected 1 arguments, but got 2.",
        ].join("\n");

        expect(diagnosticsForFile(output, PROJECT, TARGET)).toEqual([
            "generated_tests/themes/themes.spec.ts(12,7): error TS2322: Type 'string' is not assignable to type 'number'.",
        ]);
    });

    it("matches when tsc prints an absolute path", () => {
        const output = `${TARGET}(3,1): error TS2304: Cannot find name 'bar'.`;
        expect(diagnosticsForFile(output, PROJECT, TARGET)).toHaveLength(1);
    });

    it("ignores non-diagnostic noise", () => {
        const output = [
            "",
            "Found 3 errors in 2 files.",
            "Errors  Files",
            "     2  generated_tests/themes/themes.spec.ts:12",
            "npm warn exec The following package was not found",
        ].join("\n");
        // The summary lines mention the file but are not `path(l,c): error TSxxxx:`.
        expect(diagnosticsForFile(output, PROJECT, TARGET)).toEqual([]);
    });

    it("returns nothing when every error is in another file", () => {
        const output = "generated_tests/albums/albums.spec.ts(4,1): error TS2304: Cannot find name 'foo'.";
        expect(diagnosticsForFile(output, PROJECT, TARGET)).toEqual([]);
    });
});

describe("findTsconfig", () => {
    it("walks up from a nested directory to the project tsconfig", () => {
        expect(findTsconfig(join(PROJECT, "generated_tests", "_support")))
            .toBe(join(PROJECT, "tsconfig.json"));
    });

    it("returns undefined above the filesystem root", () => {
        expect(findTsconfig("/")).toBeUndefined();
    });
});

describe("typeCheckFile", () => {
    // The whole point of the change: a default import of a node builtin is
    // valid under the project's esModuleInterop, but tsc ignores tsconfig.json
    // when a file is passed positionally and used to report it as an error.
    it("accepts a support file that uses default imports of node builtins", () => {
        const result = typeCheckFile(join(PROJECT, "generated_tests", "_support", "sandbox-fs.ts"));
        expect(result).toEqual({success: true, errors: [], errorCount: 0});
    });

    it("reports a missing tsconfig rather than silently passing", () => {
        const result = typeCheckFile("/nonexistent-root-dir/file.ts");
        expect(result.success).toBe(false);
        expect(result.errors[0]).toContain("No tsconfig.json found");
    });
});
