import {afterEach, beforeEach, describe, expect, it} from "@jest/globals";
import {execFileSync} from "child_process";
import {existsSync, mkdtempSync, rmSync} from "fs";
import {tmpdir} from "os";
import {join} from "path";
import {compareShots} from "../compare";
import {toWebp, WEBP_QUALITY} from "../encode";
import {VENV_PYTHON} from "../python";

let testDir: string;

beforeEach(() => {
    testDir = mkdtempSync(join(tmpdir(), "yaffo-doc-images-"));
});

afterEach(() => {
    rmSync(testDir, {recursive: true, force: true});
});

const image = (
    name: string,
    width: number,
    height: number,
    color: [number, number, number]
): string => {
    const path = join(testDir, name);
    execFileSync(VENV_PYTHON, [
        "-c",
        "import sys;from PIL import Image;" +
        "Image.new('RGB',(int(sys.argv[2]),int(sys.argv[3]))," +
        "tuple(map(int,sys.argv[4].split(',')))).save(sys.argv[1])",
        path, String(width), String(height), color.join(","),
    ]);
    return path;
};

describe("toWebp", () => {
    it("encodes at the documented quality and removes the intermediate PNG", () => {
        const png = image("capture.png", 40, 30, [20, 40, 60]);
        const webp = toWebp(png);

        expect(WEBP_QUALITY).toBe(88);
        expect(webp).toBe(join(testDir, "capture.webp"));
        expect(existsSync(webp)).toBe(true);
        expect(existsSync(png)).toBe(false);
    });

    it("explains a missing capture before invoking Pillow", () => {
        expect(() => toWebp(join(testDir, "missing.png")))
            .toThrow(/no capture.*DOCS_CAPTURE_DIR/s);
    });
});

describe("compareShots", () => {
    it("reports pixel-identical captures as unchanged", () => {
        const baseline = image("baseline.webp", 20, 20, [10, 20, 30]);
        expect(compareShots(baseline, baseline)).toMatchObject({
            status: "unchanged",
            diffPixels: 0,
            box: null,
        });
    });

    it("forwards ignore regions to the image differ", () => {
        const baseline = image("baseline.webp", 20, 20, [0, 0, 0]);
        const candidate = image("candidate.webp", 20, 20, [255, 255, 255]);
        expect(compareShots(baseline, candidate, [
            {x: 0, y: 0, width: 20, height: 20},
        ])).toMatchObject({status: "unchanged", diffPixels: 0});
    });

    it("aligns size changes and writes their review overlay", () => {
        const baseline = image("baseline.webp", 20, 20, [0, 0, 0]);
        const candidate = image("candidate.webp", 20, 21, [0, 0, 0]);
        const overlay = join(testDir, "size-difference.png");
        expect(compareShots(baseline, candidate, [], overlay)).toMatchObject({
            status: "changed",
            reason: "size",
            baselineSize: [20, 20],
            candidateSize: [20, 21],
            diffPixels: 20,
            box: {x: 0, y: 20, width: 20, height: 1},
            diffImage: overlay,
        });
        expect(existsSync(overlay)).toBe(true);
    });

    it("writes the requested review overlay for a material change", () => {
        const baseline = image("baseline.webp", 20, 20, [0, 0, 0]);
        const candidate = image("candidate.webp", 20, 20, [255, 255, 255]);
        const overlay = join(testDir, "difference.png");
        const result = compareShots(baseline, candidate, [], overlay);

        expect(result).toMatchObject({status: "changed", diffImage: overlay});
        expect(existsSync(overlay)).toBe(true);
    });
});
