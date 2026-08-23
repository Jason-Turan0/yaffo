/**
 * A model's final answer fails in two ways, and neither may throw.
 *
 * `safeParse` guards the shape but not the parse: `JSON.parse` on a response that is
 * prose, truncated, or empty throws, and a throw at that point skips the docs
 * automation's validation gates and leaves a half-written tree behind.
 */
import {z} from "zod";
import {parseAnswer} from "@lib/user_doc_automation/tool_loop";

const Schema = z.object({
    files: z.array(z.object({filename: z.string(), code: z.string()})),
    explanation: z.string().optional(),
}).strict();

describe("parseAnswer", () => {
    it("returns the value for a well-formed answer", () => {
        const result = parseAnswer(Schema, '{"files":[{"filename":"a.md","code":"hi"}]}');
        expect(result.errors).toEqual([]);
        expect(result.value?.files[0].filename).toBe("a.md");
    });

    it("unwraps a fenced block, as non-native providers emit", () => {
        const result = parseAnswer(Schema, '```json\n{"files":[]}\n```');
        expect(result.errors).toEqual([]);
        expect(result.value?.files).toEqual([]);
    });

    it("reports prose instead of throwing", () => {
        const result = parseAnswer(Schema, "I have updated the page for you.");
        expect(result.value).toBeUndefined();
        expect(result.errors[0]).toContain("not JSON");
    });

    it("reports a truncated response instead of throwing", () => {
        const result = parseAnswer(Schema, '{"files":[{"filename":"a.md","code":"hi"');
        expect(result.value).toBeUndefined();
        expect(result.errors[0]).toContain("not JSON");
    });

    it("reports an empty response instead of throwing", () => {
        // What a turn returns when the model emits only a tool call and no text.
        const result = parseAnswer(Schema, "");
        expect(result.value).toBeUndefined();
        expect(result.errors).toHaveLength(1);
    });

    it("quotes what it got, so the failure is diagnosable", () => {
        const result = parseAnswer(Schema, "Sorry, I cannot do that.");
        expect(result.errors[0]).toContain("Sorry, I cannot do that.");
    });

    it("reports a schema mismatch with the offending path", () => {
        const result = parseAnswer(Schema, '{"files":[{"filename":"a.md"}]}');
        expect(result.value).toBeUndefined();
        expect(result.errors.join(" ")).toContain("files.0.code");
    });

    it("rejects an unexpected field rather than silently dropping it", () => {
        const result = parseAnswer(Schema, '{"files":[],"sneaky":1}');
        expect(result.value).toBeUndefined();
        expect(result.errors).not.toHaveLength(0);
    });
});
