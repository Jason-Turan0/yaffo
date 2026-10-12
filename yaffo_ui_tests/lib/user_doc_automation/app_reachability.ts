/**
 * Whether the documentation app could be reached at all.
 *
 * A walkthrough that fails because the app wasn't there (still starting, crashed,
 * the capture container off its network) says nothing about the walkthrough, and
 * must not be sent to the repair agent as a defect: it would "repair" nothing, the
 * retry would pass once the app was up, and the run would report a fix that never
 * happened.
 */

/** Prefix of the error recorded when the app never answered before a walkthrough. */
export const APP_UNREACHABLE = "app_unreachable";

// Chromium's and Node's names for "nothing answered at that address".
const UNREACHABLE = new RegExp([
    APP_UNREACHABLE,
    "ERR_CONNECTION_REFUSED", "ERR_CONNECTION_RESET", "ERR_CONNECTION_CLOSED", "ERR_EMPTY_RESPONSE",
    "ERR_NAME_NOT_RESOLVED", "ERR_ADDRESS_UNREACHABLE", "ERR_INTERNET_DISCONNECTED",
    "ECONNREFUSED", "ECONNRESET", "ENOTFOUND", "EHOSTUNREACH",
].join("|"));

export const isAppUnreachable = (error: string | undefined): boolean => Boolean(error && UNREACHABLE.test(error));

export interface WaitOptions {
    timeoutMs?: number;
    intervalMs?: number;
    fetchImpl?: typeof fetch;
}

/** Poll until the app answers any HTTP response, or the timeout passes. */
export const waitForApp = async (baseUrl: string, options: WaitOptions = {}): Promise<boolean> => {
    const {timeoutMs = 60_000, intervalMs = 2_000, fetchImpl = fetch} = options;
    const deadline = Date.now() + timeoutMs;
    for (;;) {
        try {
            await fetchImpl(baseUrl, {redirect: "manual"});
            return true;
        } catch {
            // Not answering yet.
        }
        if (Date.now() + intervalMs > deadline) return false;
        await new Promise((resolve) => setTimeout(resolve, intervalMs));
    }
};
