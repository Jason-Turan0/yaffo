import {afterEach, describe, expect, it} from "@jest/globals";
import {mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync} from "fs";
import {tmpdir} from "os";
import {dirname, join} from "path";
import {compareLayouts, describeLayoutDiff, formatElement, parseElement, SIZE_TOLERANCE} from "../layout";
import {committedLockShot, lockShotEntry, recordPromotedShot, sha256Of} from "../lockfile";
import type {ShotResult} from "../runner";

const PEOPLE = [
    'heading "People" 92×31',
    'button "Add Person" 150×44',
    'columnheader "GENDER" 68×18',
    'cell "Female" 53×18',
    'cell "Sep 12, 2014" 95×18',
];

describe("layout lines", () => {
    it("round-trips names with quotes and spaces", () => {
        const element = {role: "button", name: 'Say "hi" now', width: 120, height: 36};
        expect(parseElement(formatElement(element))).toEqual(element);
    });

    it("ignores lines it cannot read", () => {
        expect(parseElement("not a layout line")).toBeNull();
    });
});

describe("compareLayouts", () => {
    it("treats identical elements as the same", () => {
        expect(compareLayouts(PEOPLE, [...PEOPLE])).toMatchObject({same: true, added: [], removed: [], resized: []});
    });

    it("forgives sizes within the rounding tolerance", () => {
        const nudged = PEOPLE.map((line) => line.replace("92×31", `${92 + SIZE_TOLERANCE}×31`));
        expect(compareLayouts(PEOPLE, nudged).same).toBe(true);
    });

    it("reports a renamed control as removed and added", () => {
        const renamed = PEOPLE.map((line) => line.replace('"Add Person"', '"Add someone"'));
        const diff = compareLayouts(PEOPLE, renamed);
        expect(diff.same).toBe(false);
        expect(describeLayoutDiff(diff)).toEqual([
            '- removed: button "Add Person"',
            '- added: button "Add someone"',
        ]);
    });

    it("reports text that rewraps as resized", () => {
        const rewrapped = PEOPLE.map((line) => line.replace("92×31", "61×62"));
        expect(describeLayoutDiff(compareLayouts(PEOPLE, rewrapped))).toEqual([
            '- resized: heading "People" 92×31 → 61×62',
        ]);
    });

    it("keeps matching after an inserted element instead of unmatching the rest", () => {
        const inserted = [...PEOPLE.slice(0, 2), 'button "Import" 90×44', ...PEOPLE.slice(2)];
        const diff = compareLayouts(PEOPLE, inserted);
        expect(diff.added.map((element) => element.name)).toEqual(["Import"]);
        expect(diff.removed).toEqual([]);
        expect(diff.resized).toEqual([]);
    });

    it("errs towards changed when elements are reordered", () => {
        const reordered = [PEOPLE[1], PEOPLE[0], ...PEOPLE.slice(2)];
        expect(compareLayouts(PEOPLE, reordered).same).toBe(false);
    });

    it("caps a long description", () => {
        const many = Array.from({length: 20}, (_, index) => `text "line ${index}" 40×18`);
        const lines = describeLayoutDiff(compareLayouts([], many), 5);
        expect(lines).toHaveLength(6);
        expect(lines.at(-1)).toBe("- … 15 more");
    });
});

describe("lockfile shots", () => {
    const directories: string[] = [];
    afterEach(() => {
        for (const directory of directories.splice(0)) rmSync(directory, {recursive: true, force: true});
    });

    const fixture = () => {
        const root = mkdtempSync(join(tmpdir(), "yaffo-lock-"));
        directories.push(root);
        const content = join(root, "content");
        const guide = join(root, "guide");
        const target = "area/assets/page/shot.webp";
        mkdirSync(dirname(join(guide, target)), {recursive: true});
        writeFileSync(join(guide, target), "committed image");
        mkdirSync(join(content, "area", "page"), {recursive: true});
        const lock = join(content, "area", "page", "page.lock.json");
        writeFileSync(lock, JSON.stringify({
            page: "area/page",
            lastVerifiedSha: "abc",
            shots: {
                [target]: {width: 10, height: 10, sha256: sha256Of(join(guide, target)), layout: PEOPLE},
                "area/assets/page/other.webp": {width: 5, height: 5, sha256: "keep"},
            },
        }));
        const shot = {target, width: 20, height: 30, ignore: [], staged: "", status: "changed",
            layout: ['heading "People" 92×31']} as ShotResult;
        return {content, guide, target, lock, shot};
    };

    it("returns the committed entry only while its hash matches the committed image", () => {
        const {content, guide, target} = fixture();
        expect(committedLockShot(content, guide, "area/page", target)?.layout).toEqual(PEOPLE);
        writeFileSync(join(guide, target), "replaced outside the pipeline");
        expect(committedLockShot(content, guide, "area/page", target)).toBeNull();
    });

    it("records one promoted shot and leaves the rest of the lock alone", () => {
        const {content, guide, target, lock, shot} = fixture();
        writeFileSync(join(guide, target), "promoted image");

        recordPromotedShot(content, guide, "area/page", shot);

        const saved = JSON.parse(readFileSync(lock, "utf8"));
        expect(saved.lastVerifiedSha).toBe("abc");
        expect(saved.shots[target]).toEqual(lockShotEntry(shot, guide));
        expect(saved.shots[target]).toMatchObject({width: 20, height: 30, layout: ['heading "People" 92×31']});
        expect(saved.shots["area/assets/page/other.webp"]).toEqual({width: 5, height: 5, sha256: "keep"});
        expect(committedLockShot(content, guide, "area/page", target)?.layout).toEqual(['heading "People" 92×31']);
    });
});
