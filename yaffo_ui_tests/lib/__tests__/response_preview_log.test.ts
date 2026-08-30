import {describe, expect, it, jest} from "@jest/globals";
import {BaseModelClient} from "../model_clients/base_model_client";

/** Reach the protected helper without standing up a real provider client. */
const preview = (text?: string, reasoning?: string): string[] => {
    const lines: string[] = [];
    const spy = jest.spyOn(console, "log").mockImplementation((m) => {lines.push(String(m));});
    (BaseModelClient.prototype as unknown as {
        logResponsePreview(t?: string, r?: string): void;
    }).logResponsePreview(text, reasoning);
    spy.mockRestore();
    return lines;
};

describe("logResponsePreview", () => {
    it("shows the answer when there is one", () => {
        expect(preview('{"ok":true}', "some thinking")).toEqual(['   🤖 {"ok":true}']);
    });

    // The blank lines: a tool-using round returns only tool calls, so text is empty on
    // nearly every call of a run. Previously that printed nothing.
    it("falls back to the thinking when the model only called tools", () => {
        expect(preview("", "deciding which template to read")).toEqual(
            ["   💭 …deciding which template to read"]);
    });

    it("prints nothing when there is neither", () => {
        expect(preview("", "")).toEqual([]);
        expect(preview(undefined, undefined)).toEqual([]);
    });

    it("treats whitespace-only text as no answer", () => {
        expect(preview("  \n ", "thinking")).toEqual(["   💭 …thinking"]);
    });

    it("caps the answer at 200 characters", () => {
        expect(preview("y".repeat(500))[0]).toBe(`   🤖 ${"y".repeat(200)}`);
    });

    it("shows the tail of long reasoning, marked as truncated", () => {
        const line = preview("", "z".repeat(500))[0];
        expect(line).toBe(`   💭 …${"z".repeat(200)}`);
    });

    it("never labels reasoning as if it were the answer", () => {
        expect(preview("", '{"files":[]}')[0]).toMatch(/^ +💭/);
    });
});
