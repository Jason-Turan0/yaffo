/**
 * Detector B's core: which of the app's user-visible strings a guide page quotes, and
 * which of those the app has stopped saying.
 *
 * The extraction and matching are pure and hold the design decisions worth pinning —
 * that matching is confined to emphasised spans, and that the two catalogues carry
 * different amounts of information about the same rename.
 */
import {
    changesQuotedBy,
    diffCatalogues,
    emphasisedSpans,
    jsonStrings,
    potStrings,
} from "@lib/user_doc_automation/strings";
import type {StringChange} from "@lib/user_doc_automation/strings";

describe("potStrings", () => {
    it("extracts msgids", () => {
        const pot = [
            'msgid "Apply Filters"', 'msgstr ""', "",
            'msgid "Clear Filters"', 'msgstr ""',
        ].join("\n");
        expect(potStrings(pot)).toEqual(new Set(["Apply Filters", "Clear Filters"]));
    });

    it("ignores the empty header entry", () => {
        // Every .pot opens with `msgid ""`, which is metadata, not a UI string.
        const pot = ['msgid ""', 'msgstr "Project-Id-Version: yaffo"', "", 'msgid "Save"'].join("\n");
        expect(potStrings(pot)).toEqual(new Set(["Save"]));
    });

    it("returns nothing for an empty catalogue", () => {
        expect(potStrings("").size).toBe(0);
    });
});

describe("jsonStrings", () => {
    it("flattens namespaces into dotted keys", () => {
        const json = JSON.stringify({common: {apply: "Apply"}, media: {gallery: {play: "Play video"}}});
        expect(jsonStrings(json)).toEqual(new Map([
            ["common.apply", "Apply"],
            ["media.gallery.play", "Play video"],
        ]));
    });

    it("survives a malformed catalogue rather than throwing", () => {
        // One end of a diff may be mid-edit; that is not worth failing a run over.
        expect(jsonStrings("{not json").size).toBe(0);
    });
});

describe("emphasisedSpans", () => {
    it("finds bold and code spans", () => {
        const md = "Click **Apply Filters** to update, or type `dog` to search.";
        expect(emphasisedSpans(md)).toEqual(new Set(["Apply Filters", "dog"]));
    });

    it("does not run across a line break", () => {
        // Stray asterisks on separate lines must not pair up. Without the newline guard
        // this captures "opens here.\nand closes", swallowing a paragraph of prose as
        // though it were a control name.
        const md = "A line that **opens here.\nand closes** on the next.";
        expect(emphasisedSpans(md).size).toBe(0);
    });

    it("ignores plain prose", () => {
        expect(emphasisedSpans("Click Apply Filters to update.").size).toBe(0);
    });
});

describe("changesQuotedBy", () => {
    const removed: StringChange = {was: "Clear Filters", source: "messages.pot"};
    const reworded: StringChange = {
        was: "Sync Database", now: "Synchronise Library",
        source: "en.json", key: "utilities.indexPhotos.sync.button",
    };

    it("flags a change the page quotes as a control", () => {
        const md = "Click **Clear Filters** to return to the unfiltered gallery.";
        expect(changesQuotedBy(md, [removed])).toEqual([removed]);
    });

    it("ignores a page that does not mention it", () => {
        expect(changesQuotedBy("Nothing relevant here.", [removed])).toEqual([]);
    });

    it("ignores the same words in ordinary prose", () => {
        // The precision decision: bare substring matching finds half again as many hits,
        // almost all of them words like "All" and "Year" in running text. A control
        // written in bold is the one the reader is told to click.
        const md = "You may want to clear filters first before trying again.";
        expect(changesQuotedBy(md, [{was: "clear filters", source: "messages.pot"}])).toEqual([]);
    });

    it("matches the whole span, not a substring of it", () => {
        const md = "Click **Clear Filters and Reset** to start over.";
        expect(changesQuotedBy(md, [removed])).toEqual([]);
    });

    it("keeps the replacement text when the catalogue provides one", () => {
        // The asymmetry between the catalogues: a reworded JSON value carries both
        // sides, where a reworded msgid can only ever look like a disappearance.
        const md = "Press **Sync Database** to import new files.";
        const [flagged] = changesQuotedBy(md, [reworded]);
        expect(flagged.now).toBe("Synchronise Library");
        expect(changesQuotedBy("Press **Clear Filters** now.", [removed])[0].now).toBeUndefined();
    });

    it("reports every quoted change on a page, not just the first", () => {
        const md = "Use **Clear Filters**, then **Sync Database**.";
        expect(changesQuotedBy(md, [removed, reworded])).toHaveLength(2);
    });
});

describe("diffCatalogues", () => {
    const pot = (...ids: string[]) => ids.map((id) => `msgid "${id}"\nmsgstr ""`).join("\n\n");
    const json = (values: Record<string, string>) =>
        JSON.stringify({common: values});

    it("reports a msgid that disappeared", () => {
        const changes = diffCatalogues(
            {pot: pot("Apply Filters", "Save"), json: null},
            {pot: pot("Run Filters", "Save"), json: null});
        expect(changes).toEqual([{was: "Apply Filters", source: "messages.pot"}]);
    });

    it("reports a reworded JSON value with both sides", () => {
        // The whole reason both catalogues are read: a stable key makes the replacement
        // recoverable, where a reworded msgid only ever looks like a deletion.
        const changes = diffCatalogues(
            {pot: null, json: json({apply: "Apply"})},
            {pot: null, json: json({apply: "Apply Now"})});
        expect(changes).toEqual([
            {was: "Apply", now: "Apply Now", source: "en.json", key: "common.apply"},
        ]);
    });

    it("reports a deleted JSON key with no replacement", () => {
        const changes = diffCatalogues(
            {pot: null, json: json({apply: "Apply", cancel: "Cancel"})},
            {pot: null, json: json({cancel: "Cancel"})});
        expect(changes).toStrictEqual([{was: "Apply", source: "en.json", key: "common.apply"}]);
    });

    it("ignores additions", () => {
        // A new string is a new feature — an incompleteness question, which scoping
        // answers by flagging pages whose dependencies changed.
        const changes = diffCatalogues(
            {pot: pot("Save"), json: json({apply: "Apply"})},
            {pot: pot("Save", "Publish"), json: json({apply: "Apply", reset: "Reset"})});
        expect(changes).toEqual([]);
    });

    it("reports nothing when neither catalogue moved", () => {
        const before = {pot: pot("Save"), json: json({apply: "Apply"})};
        expect(diffCatalogues(before, {...before})).toEqual([]);
    });

    it("skips a catalogue missing from either side", () => {
        // A file absent at one end of the diff — added or removed between the two
        // commits — cannot be compared, and must not read as everything vanishing.
        expect(diffCatalogues({pot: pot("Save"), json: null}, {pot: null, json: null})).toEqual([]);
    });
});
