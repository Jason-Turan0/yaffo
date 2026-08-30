import {afterEach, beforeEach, describe, expect, it, jest} from "@jest/globals";

const execFileSync = jest.fn<(
    file: string, args: string[], options: Record<string, unknown>
) => string>();
const readFileSync = jest.fn<(path: string, encoding: string) => string>();
let warn: jest.SpiedFunction<typeof console.warn>;

await jest.unstable_mockModule("child_process", () => ({execFileSync}));
await jest.unstable_mockModule("fs", () => ({readFileSync}));

const {CATALOGUE_FILES, changedStrings} = await import("../strings");

const beforePot = 'msgid "Save"\nmsgstr ""\n\nmsgid "Clear Filters"\nmsgstr ""';
const afterPot = 'msgid "Save"\nmsgstr ""';
const beforeJson = JSON.stringify({common: {apply: "Apply Filters", save: "Save"}});
const afterJson = JSON.stringify({common: {apply: "Apply", save: "Save"}});

beforeEach(() => {
    execFileSync.mockReset();
    readFileSync.mockReset();
    warn = jest.spyOn(console, "warn").mockImplementation(() => undefined);
});

afterEach(() => warn.mockRestore());

describe("changedStrings catalogue loading", () => {
    it("compares a committed watermark with working-tree English catalogues", () => {
        execFileSync.mockImplementation((_file, args) =>
            args[1].endsWith("messages.pot") ? beforePot : beforeJson);
        readFileSync.mockImplementation((path) =>
            path.endsWith("messages.pot") ? afterPot : afterJson);

        expect(changedStrings("verified-sha")).toEqual([
            {was: "Clear Filters", source: "messages.pot"},
            {was: "Apply Filters", now: "Apply", source: "en.json", key: "common.apply"},
        ]);

        expect(execFileSync.mock.calls.map((call) => call[1])).toEqual([
            ["show", "verified-sha:messages.pot"],
            ["show", "verified-sha:yaffo/static/locales/en.json"],
        ]);
        expect(execFileSync.mock.calls[0][2]).toEqual(expect.objectContaining({
            stdio: ["ignore", "pipe", "pipe"],
        }));
        expect(readFileSync).toHaveBeenCalledTimes(2);
        expect(warn).not.toHaveBeenCalled();
    });

    it("reads both sides through git when an explicit head is supplied", () => {
        execFileSync.mockImplementation((_file, args) => {
            const refPath = args[1];
            if (refPath === "base:messages.pot") return beforePot;
            if (refPath === "head:messages.pot") return afterPot;
            if (refPath === "base:yaffo/static/locales/en.json") return beforeJson;
            return afterJson;
        });

        expect(changedStrings("base", "head")).toHaveLength(2);
        expect(readFileSync).not.toHaveBeenCalled();
        expect(execFileSync).toHaveBeenCalledTimes(4);
    });

    it("treats unreadable catalogues as unavailable instead of reporting mass deletion", () => {
        execFileSync.mockImplementation(() => {
            throw new Error("unknown revision");
        });
        readFileSync.mockImplementation(() => {
            throw new Error("file missing");
        });

        expect(changedStrings("missing-ref")).toEqual([]);
        expect(warn.mock.calls.map(([message]) => message)).toEqual([
            "String catalogue messages.pot is unavailable at watermark missing-ref; " +
                "skipping its quoted-string comparison.",
            "String catalogue yaffo/static/locales/en.json is unavailable at watermark missing-ref; " +
                "skipping its quoted-string comparison.",
            "String catalogue messages.pot is unavailable at the working tree; " +
                "skipping its quoted-string comparison.",
            "String catalogue yaffo/static/locales/en.json is unavailable at the working tree; " +
                "skipping its quoted-string comparison.",
        ]);
    });

    it("limits detection to the two English source catalogues", () => {
        expect(CATALOGUE_FILES).toEqual([
            "messages.pot",
            "yaffo/static/locales/en.json",
        ]);
    });
});
