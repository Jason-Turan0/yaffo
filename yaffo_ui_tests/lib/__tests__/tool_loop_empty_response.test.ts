import {runToolLoop} from "../user_doc_automation/tool_loop";
import type {ModelClient, ModelResponse} from "../model_clients/model_client.interface";

/** Minimal stand-in: runToolLoop only touches these three members. */
const fakeClient = (responses: Array<Partial<ModelResponse> | undefined>): ModelClient => {
    let i = 0;
    return {
        callModelApi: async () => {
            const next = responses[i++];
            return next && {toolCalls: [], text: "", ...next} as ModelResponse;
        },
        addToolResultMessage: () => {},
        lastError: undefined,
    } as unknown as ModelClient;
};

describe("runToolLoop on an empty final answer", () => {
    // Regression: a DeepSeek generate turn spent all 16000 output tokens on hidden
    // reasoning and returned "". That reached the caller as "response was not JSON",
    // which points at the prompt rather than at the output budget.
    it("blames the output budget, not the JSON", async () => {
        await expect(runToolLoop(fakeClient([{text: ""}]), [])).rejects.toThrow(
            /returned no text.*setMaxOutputTokens/s);
    });

    it("reports the finish reason when the provider gave one", async () => {
        await expect(runToolLoop(fakeClient([{text: "", finishReason: "length"}]), []))
            .rejects.toThrow(/finishReason: length/);
    });

    it("reports reasoning tokens when the provider gave them", async () => {
        const usage = {reasoningTokens: 16000} as unknown as ModelResponse["usage"];
        await expect(runToolLoop(fakeClient([{text: "", usage}]), []))
            .rejects.toThrow(/16000 tokens on reasoning/);
    });

    it("treats whitespace as empty", async () => {
        await expect(runToolLoop(fakeClient([{text: "  \n "}]), [])).rejects.toThrow(
            /returned no text/);
    });

    it("still returns a real answer untouched", async () => {
        await expect(runToolLoop(fakeClient([{text: '{"ok":true}'}]), []))
            .resolves.toBe('{"ok":true}');
    });

    it("distinguishes no response at all from an empty one", async () => {
        await expect(runToolLoop(fakeClient([undefined]), [])).rejects.toThrow(
            /returned no response/);
    });
});

describe("reasoning text is diagnostic, never the answer", () => {
    const withThinking = (text: string, reasoningText: string) =>
        runToolLoop(fakeClient([{text, reasoningText}]), []);

    it("shows the tail of the thinking so the failure explains itself", async () => {
        await expect(withThinking("", "...deciding whether clicking Open File is safe"))
            .rejects.toThrow(/last thought before it stopped: ….*Open File is safe/s);
    });

    // The point of the separation: reasoning is prose, callers expect JSON. Returning
    // it would turn a clean truncation error into a plausible wrong answer.
    it("never substitutes reasoning for a missing answer", async () => {
        await expect(withThinking("", '{"files":[]}')).rejects.toThrow(/returned no text/);
    });

    it("does not touch a real answer that also carries reasoning", async () => {
        await expect(withThinking('{"ok":true}', "some thinking"))
            .resolves.toBe('{"ok":true}');
    });

    it("truncates very long reasoning rather than dumping it", async () => {
        await expect(withThinking("", "x".repeat(50000)))
            .rejects.toThrow(/…x{300}$/);
    });
});
