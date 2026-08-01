import fs from "node:fs";
import path from "node:path";
import {tmpdir} from "node:os";
import {
    buildDuplicateImageCorpus,
    copyPhotoWithUniqueMarker,
    countEntriesIn,
    ensureTempDir,
    findAnyPhotoIn,
    listFilesRecursive,
    listSubdirectories,
    removeTempDirs,
    removeTempFile,
    resetTempDir,
} from "../../generated_tests/_support/sandbox-fs";

const scratch = fs.mkdtempSync(path.join(tmpdir(), "sandbox-fs-test-"));

afterAll(() => {
    fs.rmSync(scratch, {recursive: true, force: true});
});

describe("sandbox-fs temp-root guard", () => {
    it("refuses writes outside the temp root", () => {
        expect(() => ensureTempDir("/etc/spec-evil")).toThrow(/outside the temp root/);
        expect(() => resetTempDir(path.join(process.cwd(), "lib"))).toThrow(/outside the temp root/);
        expect(() => removeTempDirs("/usr/local")).toThrow(/outside the temp root/);
        expect(() => removeTempFile("/etc/hosts")).toThrow(/outside the temp root/);
    });

    it("refuses temp-escaping traversal", () => {
        expect(() => ensureTempDir(path.join(tmpdir(), "..", "spec-escape"))).toThrow(/outside the temp root/);
    });

    it("refuses reads outside temp root and test_data", () => {
        expect(() => listFilesRecursive(path.join(process.cwd(), "lib"))).toThrow(/Refusing to read/);
        expect(() => listSubdirectories("/etc")).toThrow(/Refusing to read/);
    });
});

describe("sandbox-fs operations", () => {
    it("creates, lists, and removes temp directories", () => {
        const dir = path.join(scratch, "nested", "dir");
        ensureTempDir(dir);
        fs.writeFileSync(path.join(dir, "a.txt"), "x");
        fs.writeFileSync(path.join(scratch, "nested", "b.txt"), "y");
        expect(countEntriesIn(dir)).toBe(1);
        expect(listFilesRecursive(path.join(scratch, "nested")).length).toBe(2);
        expect(listSubdirectories(path.join(scratch, "nested"))).toEqual(["dir"]);
        resetTempDir(dir);
        expect(countEntriesIn(dir)).toBe(0);
        removeTempDirs(path.join(scratch, "nested"), undefined);
        expect(fs.existsSync(path.join(scratch, "nested"))).toBe(false);
    });

    it("builds a duplicate corpus from distinct fixture photos", () => {
        const scanDir = path.join(scratch, "dup-scan");
        const destDir = path.join(scratch, "dup-dest");
        buildDuplicateImageCorpus(scanDir, destDir, 12);
        const files = fs.readdirSync(scanDir).sort();
        expect(files).toHaveLength(24);
        // Each group is one photo copied twice — identical within the pair…
        const a = fs.readFileSync(path.join(scanDir, "dup-0-a.jpg"));
        const b = fs.readFileSync(path.join(scanDir, "dup-0-b.jpg"));
        expect(a.equals(b)).toBe(true);
        // …and different photos across groups.
        const other = fs.readFileSync(path.join(scanDir, "dup-1-a.jpg"));
        expect(a.equals(other)).toBe(false);
    });

    it("finds and copies photos with a uniqueness marker", () => {
        const dir = path.join(scratch, "photos");
        buildDuplicateImageCorpus(dir, path.join(scratch, "unused"), 1);
        const source = findAnyPhotoIn(dir);
        const dest = path.join(dir, "copy.jpg");
        copyPhotoWithUniqueMarker(source, dest, "marker-123");
        const copied = fs.readFileSync(dest);
        expect(copied.length).toBeGreaterThan(fs.readFileSync(source).length);
        expect(copied.subarray(-10).toString()).toContain("marker-123");
        removeTempFile(dest);
        expect(fs.existsSync(dest)).toBe(false);
    });
});
