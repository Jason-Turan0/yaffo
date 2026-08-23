import {describe, expect, it} from "@jest/globals";
import {recoverMalformedFunctionCall} from "../model_clients/gemini_recovery";

const body = (candidate: Record<string, unknown>): string =>
    JSON.stringify({candidates: [candidate]});

const ANSWER = '{"files": [{"filename": "a.md", "code": "x"}], "confidence": 1}';

describe("recoverMalformedFunctionCall", () => {
    it("recovers a fenced answer Gemini discarded", () => {
        expect(recoverMalformedFunctionCall(body({
            finishReason: "MALFORMED_FUNCTION_CALL",
            finishMessage: "Malformed function call: ```json\n" + ANSWER + "\n```",
        }))).toBe(ANSWER);
    });

    it("recovers an unfenced answer", () => {
        expect(recoverMalformedFunctionCall(body({
            finishReason: "MALFORMED_FUNCTION_CALL",
            finishMessage: `Malformed function call: ${ANSWER}`,
        }))).toBe(ANSWER);
    });

    // Only ever a last resort: a candidate with real content is the answer.
    it("never overrides content the model actually returned", () => {
        expect(recoverMalformedFunctionCall(body({
            finishReason: "MALFORMED_FUNCTION_CALL",
            finishMessage: "Malformed function call: " + ANSWER,
            content: {parts: [{text: '{"real": true}'}]},
        }))).toBeUndefined();
    });

    it.each([
        ["a normal stop", {finishReason: "STOP", finishMessage: "x"}],
        ["truncation", {finishReason: "MAX_TOKENS"}],
        ["no finish message", {finishReason: "MALFORMED_FUNCTION_CALL"}],
        ["an empty finish message", {finishReason: "MALFORMED_FUNCTION_CALL", finishMessage: "   "}],
    ])("leaves %s alone", (_label, candidate) => {
        expect(recoverMalformedFunctionCall(body(candidate))).toBeUndefined();
    });

    it.each([["undefined", undefined], ["not JSON", "<html>500</html>"], ["no candidates", "{}"]])(
        "survives %s", (_label, raw) => {
            expect(recoverMalformedFunctionCall(raw as string | undefined)).toBeUndefined();
        });
});
