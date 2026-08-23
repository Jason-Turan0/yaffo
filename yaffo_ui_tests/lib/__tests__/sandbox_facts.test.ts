import {describe, expect, it} from "@jest/globals";
import {describeSandboxFacts, PRIMARY_DETAIL_IMAGE} from "../user_doc_automation/sandbox_facts";
import type {SandboxFacts} from "../user_doc_automation/sandbox_facts";

const facts = (over: Partial<SandboxFacts> = {}): SandboxFacts =>
    ({photos: ["a-photo.png"], videos: ["a-video.mp4"], ...over});

describe("describeSandboxFacts", () => {
    // The whole point: ids are assigned at index time and change on every reseed, so
    // putting one in the prompt — even "for orientation" — invites a walkthrough that
    // documents whichever item lands at that number next time.
    it("never reports a numeric media id", () => {
        const text = describeSandboxFacts(facts());
        expect(text).not.toMatch(/\/media\/view\/\d+["`\s]*$/m);
        expect(text).not.toMatch(/\bid \d+\b/);
    });

    it("lists the filenames it did find", () => {
        const text = describeSandboxFacts(facts({photos: ["x.png"], videos: ["y.mp4"]}));
        expect(text).toContain("`x.png`");
        expect(text).toContain("`y.mp4`");
    });

    it("shows the filename-resolving pattern, pinned to the shared fixture file", () => {
        const text = describeSandboxFacts(facts());
        expect(text).toContain(PRIMARY_DETAIL_IMAGE);
        expect(text).toContain("mediaIdByFilename");
    });

    // These exist so duplicate detection has something to find; a shot of one is
    // colour bars, not a photo library.
    it("warns off the synthetic test patterns", () => {
        const text = describeSandboxFacts(facts({videos: ["1mb-example-video-file.mp4"]}));
        expect(text).toMatch(/1mb-example-video-file\.mp4`\s+← synthetic test pattern/);
    });

    it("leaves real media unflagged", () => {
        const text = describeSandboxFacts(facts({videos: ["2021-07-11_boy-and-the-waves.mp4"]}));
        expect(text).toMatch(/boy-and-the-waves\.mp4`$/m);
    });

    it("says so plainly when the sandbox could not be read", () => {
        const text = describeSandboxFacts(facts({error: "ECONNREFUSED"}));
        expect(text).toContain("ECONNREFUSED");
        expect(text).toMatch(/do not guess/);
    });

    it("handles a kind being absent rather than printing an empty list", () => {
        expect(describeSandboxFacts(facts({videos: []}))).toContain("(none in the fixture)");
    });

    it("caps each list so the prompt stays readable", () => {
        const many = Array.from({length: 40}, (_, i) => `photo-${i}.png`);
        const text = describeSandboxFacts(facts({photos: many}));
        expect(text).toContain("`photo-7.png`");
        expect(text).not.toContain("`photo-8.png`");
    });
});
