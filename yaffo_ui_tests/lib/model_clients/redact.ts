/**
 * Strip credentials from anything about to be written to disk.
 *
 * Deliberately dependency-free so it can be unit tested without loading the AI
 * SDK, and so the write path cannot acquire a reason to skip it.
 */
/** Header names whose values are credentials, lowercased for comparison. */
const SECRET_HEADERS = new Set([
    "authorization",
    "x-api-key",
    "api-key",
    "x-goog-api-key",
    "anthropic-api-key",
]);

export const REDACTED = "[redacted]";

/**
 * Strip credentials from anything about to be written to disk.
 *
 * Run logs capture the request verbatim, headers included, so an unredacted log
 * contains a usable provider key in plaintext — and these logs are exactly what CI
 * would upload as build artifacts. Redaction happens at the write, not at the call
 * sites, so every provider is covered and a new one cannot forget.
 *
 * Structural: any header-shaped key is redacted wherever it appears, rather than
 * matching on value patterns, which would miss a key format nobody anticipated.
 */
export const redactSecrets = <T>(value: T): T => {
    const walk = (node: unknown): unknown => {
        if (Array.isArray(node)) return node.map(walk);
        if (node && typeof node === "object") {
            return Object.fromEntries(
                Object.entries(node as Record<string, unknown>).map(([key, child]) =>
                    SECRET_HEADERS.has(key.toLowerCase())
                        ? [key, REDACTED]
                        : [key, walk(child)]),
            );
        }
        return node;
    };
    return walk(value) as T;
};
