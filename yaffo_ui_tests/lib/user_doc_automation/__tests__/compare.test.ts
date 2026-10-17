import {afterEach, describe, expect, it, jest} from "@jest/globals";
import {existsSync, mkdtempSync, readFileSync, rmSync} from "fs";
import {tmpdir} from "os";
import {join} from "path";
import {fileURLToPath} from "url";
import {compareShots, renderReviewSheet} from "../compare";

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
        expect(result.change).toBe("changed");
        expect(result.diffPixels).toBe(781);
        expect(existsSync(diff)).toBe(true);
    });

    it("reports nothing moved when only pixels changed in place", () => {
        const {result} = compare("text-base.webp", "text-count-change.webp");

        expect(result.change).toBe("changed");
        expect(result.residualPixels).toBe(113);
        expect(result.hunks).toEqual([]);
    });
});

describe("row-aligned comparison", () => {
    // PR 63: the navbar's pages strip became hidden by default, the Ask Yaffo panel's
    // empty conversation area grew 36 CSS px, and the composer moved down. A top-left
    // pixel diff saw a reframed shot; the model then called it environment noise.
    it("reports PR 63's pushed-down composer as layout only", () => {
        const {result, diff} = compare("pr63-context-base.webp", "pr63-context-candidate.webp");

        expect(result).toMatchObject({
            status: "changed",
            change: "layout-only",
            residualPixels: 0,
            reason: "size",
            baselineSize: [912, 1846],
            candidateSize: [912, 1918],
        });
        const edits = (result.hunks ?? []).filter((hunk) => hunk.op !== "equal");
        expect(edits).toEqual([expect.objectContaining({op: "insert", added: "blank"})]);
        const [top, bottom] = edits[0].candidate;
        expect(bottom - top).toBe(72);
        // The band sits above the composer, not at the bottom of the frame: the rows
        // below it are the composer, matched 72 px further down.
        expect(result.hunks?.at(-1)).toMatchObject({op: "equal", offset: 72});
        expect(existsSync(diff)).toBe(true);
    });

    // Synthetic: a table whose GENDER, BORN and FACES columns moved right by 40, 70
    // and 90 px, cells pixel-identical. Every row matches in place yet repaints, so
    // only aligning columns explains it.
    it("reports widened table columns as layout only, band by band", () => {
        const {result} = compare("columns-base.webp", "columns-candidate.webp");

        expect(result).toMatchObject({status: "changed", change: "layout-only", residualPixels: 0});
        const inserted = (result.blocks ?? []).filter((block) => block.axis === "columns" && block.op === "insert");
        expect(inserted.map((block) => block.candidate[1] - block.candidate[0])).toEqual([40, 30, 20]);
        expect(inserted.every((block) => block.added === "blank")).toBe(true);
        const moved = (result.blocks ?? []).filter((block) => block.op === "equal").map((block) => block.offset);
        expect(moved).toEqual([40, 70, 90]);
    });
});

describe("review sheet", () => {
    it("writes a WebP sheet for a changed shot", () => {
        const directory = mkdtempSync(join(tmpdir(), "yaffo-review-"));
        temporaryDirectories.push(directory);
        const out = join(directory, "gallery.review.webp");

        expect(renderReviewSheet(fixture("columns-base.webp"), fixture("columns-candidate.webp"), [],
            out, "area/assets/page/gallery.webp")).toBe(true);

        expect(existsSync(out)).toBe(true);
        // RIFF....WEBP: a real WebP, not a renamed PNG.
        const header = readFileSync(out).subarray(0, 12).toString("latin1");
        expect(header.startsWith("RIFF") && header.endsWith("WEBP")).toBe(true);
    });

    it("reports and skips a sheet it cannot draw instead of failing the capture", () => {
        const directory = mkdtempSync(join(tmpdir(), "yaffo-review-"));
        temporaryDirectories.push(directory);
        const warn = jest.spyOn(console, "warn").mockImplementation(() => undefined);
        try {
            expect(renderReviewSheet(join(directory, "missing.webp"), fixture("columns-candidate.webp"), [],
                join(directory, "out.webp"), "area/assets/page/missing.webp")).toBe(false);
            expect(warn).toHaveBeenCalledWith(expect.stringContaining("review sheet not written"));
        } finally {
            warn.mockRestore();
        }
    });
});
