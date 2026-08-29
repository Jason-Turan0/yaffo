import {afterEach, describe, expect, it} from "@jest/globals";
import {existsSync, mkdtempSync, rmSync} from "fs";
import {tmpdir} from "os";
import {join} from "path";
import {fileURLToPath} from "url";
import {compareShots} from "../compare";

const FIXTURES = join(fileURLToPath(new URL(".", import.meta.url)), "fixtures", "imagediff");
const temporaryDirectories: string[] = [];

const fixture = (name: string): string => join(FIXTURES, name);

const compare = (baseline: string, candidate: string) => {
    const directory = mkdtempSync(join(tmpdir(), "yaffo-imagediff-"));
    temporaryDirectories.push(directory);
    return {
        result: compareShots(fixture(baseline), fixture(candidate), [], join(directory, "diff.png")),
        diff: join(directory, "diff.png"),
    };
};

afterEach(() => {
    for (const directory of temporaryDirectories.splice(0)) {
        rmSync(directory, {recursive: true, force: true});
    }
});

describe("Playwright-style screenshot comparison", () => {
    it("ignores PR 15's semantically identical glyph-rasterization drift", () => {
        const {result, diff} = compare("pr15-base.webp", "pr15-candidate.webp");

        expect(result).toMatchObject({
            status: "unchanged",
            diffPixels: 72,
            totalPixels: 5_497_800,
        });
        expect(existsSync(diff)).toBe(false);
    });

    it("still catches a single-digit count change", () => {
        const {result, diff} = compare("text-base.webp", "text-count-change.webp");

        expect(result.status).toBe("changed");
        expect(result.diffPixels).toBe(113);
        expect(result.box).toMatchObject({width: expect.any(Number), height: expect.any(Number)});
        expect(existsSync(diff)).toBe(true);
    });

    it("still catches a short control-label change", () => {
        const {result, diff} = compare("text-base.webp", "text-label-change.webp");

        expect(result.status).toBe("changed");
        expect(result.diffPixels).toBe(781);
        expect(existsSync(diff)).toBe(true);
    });
});
