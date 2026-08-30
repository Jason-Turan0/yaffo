/**
 * Run logs capture requests verbatim, headers included, and the plan has CI
 * uploading them as build artifacts. An unredacted log therefore publishes a usable
 * provider key.
 */
import {REDACTED, redactSecrets} from "@lib/model_clients/redact";

describe("redactSecrets", () => {
    it("redacts a bearer token in request headers", () => {
        const entry = {
            request: {
                url: "https://api.deepseek.com/chat/completions",
                headers: {
                    authorization: "Bearer sk-secret-value",
                    "content-type": "application/json",
                },
            },
        };
        const safe = redactSecrets(entry);
        expect(safe.request.headers.authorization).toBe(REDACTED);
        expect(JSON.stringify(safe)).not.toContain("sk-secret-value");
    });

    it("leaves everything else intact", () => {
        const entry = {
            request: {headers: {authorization: "Bearer x", "user-agent": "ai/7.0.47"}},
            response: {model: "gemini-3.6-flash", choices: [{message: {content: "hello"}}]},
            success: true,
        };
        const safe = redactSecrets(entry);
        expect(safe.request.headers["user-agent"]).toBe("ai/7.0.47");
        expect(safe.response.choices[0].message.content).toBe("hello");
        expect(safe.success).toBe(true);
    });

    it("matches header names case-insensitively", () => {
        const safe = redactSecrets({headers: {Authorization: "Bearer x", "X-Api-Key": "k"}});
        expect(safe.headers.Authorization).toBe(REDACTED);
        expect(safe.headers["X-Api-Key"]).toBe(REDACTED);
    });

    it("redacts the other providers' key headers", () => {
        for (const header of ["x-api-key", "api-key", "x-goog-api-key", "anthropic-api-key"]) {
            const safe = redactSecrets({request: {headers: {[header]: "secret"}}});
            expect(safe.request.headers[header]).toBe(REDACTED);
        }
    });

    it("finds credentials at any depth", () => {
        // Providers nest the request differently, so redaction is structural rather
        // than tied to one known shape.
        const safe = redactSecrets({a: {b: [{c: {headers: {authorization: "Bearer deep"}}}]}});
        expect(JSON.stringify(safe)).not.toContain("Bearer deep");
    });

    it("does not mutate the entry it was given", () => {
        const entry = {request: {headers: {authorization: "Bearer keep"}}};
        redactSecrets(entry);
        expect(entry.request.headers.authorization).toBe("Bearer keep");
    });

    it("passes through values that are not objects", () => {
        expect(redactSecrets(null)).toBeNull();
        expect(redactSecrets("text")).toBe("text");
        expect(redactSecrets(7)).toBe(7);
    });
});
