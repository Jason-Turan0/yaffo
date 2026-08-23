/**
 * Recover an answer Gemini threw away.
 *
 * When tools are declared, Gemini sometimes decides a plain JSON answer was meant to be
 * a function call, fails to parse it as one, and returns `finishReason:
 * MALFORMED_FUNCTION_CALL` with **no candidate content at all**. The answer is not
 * lost — it is sitting in `finishMessage`, complete and well-formed:
 *
 *   "finishReason": "MALFORMED_FUNCTION_CALL",
 *   "finishMessage": "Malformed function call: ```json\n{\n  \"files\": [ ... ] }\n```"
 *
 * A 7000-character generate answer with both files in it was discarded this way, and
 * surfaced downstream as "the model returned no text" — which reads like truncation and
 * sent the retry after the output budget instead. Recovering it costs one string
 * operation and saves a whole turn.
 *
 * Returns the answer text, or undefined when there is nothing to recover.
 */
export const recoverMalformedFunctionCall = (rawBody: string | undefined): string | undefined => {
    if (!rawBody) return undefined;

    let parsed: unknown;
    try {
        parsed = JSON.parse(rawBody);
    } catch {
        return undefined;
    }

    const candidate = (parsed as {candidates?: Array<{
        finishReason?: string;
        finishMessage?: string;
        content?: {parts?: Array<{text?: string}>};
    }>})?.candidates?.[0];
    if (candidate?.finishReason !== "MALFORMED_FUNCTION_CALL") return undefined;

    // Never override a real answer: only step in when the candidate carries no content.
    const existing = candidate.content?.parts?.map((part) => part.text ?? "").join("") ?? "";
    if (existing.trim()) return undefined;

    const message = candidate.finishMessage?.trim();
    if (!message) return undefined;

    // "Malformed function call: <the answer>"
    const body = message.replace(/^Malformed function call:\s*/, "").trim();
    if (!body) return undefined;

    // The answer is usually fenced. `extractJson` downstream tolerates a fence, but
    // stripping it here keeps what is stored as the assistant turn clean.
    const fenced = body.match(/^```(?:json)?\s*\n([\s\S]*?)\n?```$/);
    return (fenced ? fenced[1] : body).trim() || undefined;
};
