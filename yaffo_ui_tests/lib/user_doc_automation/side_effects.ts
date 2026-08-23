import type {BrowserContext} from "@playwright/test";

/**
 * Endpoints that reach outside the browser, stubbed for the duration of a capture.
 *
 * `/api/open-file` and `/api/open-folder` run `subprocess.run(["open", path])` on
 * macOS (`xdg-open` on Linux, `os.startfile` on Windows) against the *real* file. The
 * guide documents both controls, so a walkthrough for `photo-details` reasonably clicks
 * them — and every capture run then opens Preview windows on whoever's machine is
 * running it. In CI it would spawn processes on the runner.
 *
 * Stubbed rather than aborted: the page gets the same `{"success": true}` a real call
 * returns, so the UI reaches the state the screenshot is meant to show. Aborting would
 * surface an error toast and document a failure.
 *
 * This is containment, not correctness — a walkthrough that clicks these is doing
 * something reasonable, and the framework should make it safe rather than forbid it.
 */
export const OS_SIDE_EFFECT_ROUTES = [
    "**/api/open-file",
    "**/api/open-folder",
];

/** Matches what the real endpoints return on success. */
const STUB_BODY = JSON.stringify({success: true});

export const blockOsSideEffects = async (context: BrowserContext): Promise<void> => {
    for (const pattern of OS_SIDE_EFFECT_ROUTES) {
        await context.route(pattern, (route) => route.fulfill({
            status: 200,
            contentType: "application/json",
            body: STUB_BODY,
        }));
    }
};
