import type {BrowserContext} from "@playwright/test";

/**
 * Endpoints that reach outside the browser, stubbed for the duration of a capture.
 *
 * `POST /api/openings` (the media page's Open File / Open Folder, and the assistant's
 * open links) runs `subprocess.run(["open", path])` on macOS (`xdg-open` on Linux,
 * `os.startfile` on Windows) against the *real* file. The guide documents those
 * controls, so a walkthrough for `photo-details` reasonably clicks them — and every
 * capture run then opens Preview windows on whoever's machine is running it. In CI it
 * would spawn processes on the runner.
 *
 * Stubbed rather than aborted: the page gets the same empty `202` a real call returns,
 * so the UI reaches the state the screenshot is meant to show. Aborting would surface
 * an error toast and document a failure.
 *
 * This is containment, not correctness — a walkthrough that clicks these is doing
 * something reasonable, and the framework should make it safe rather than forbid it.
 */
export const OS_SIDE_EFFECT_ROUTES = [
    "**/api/openings",
];

export const blockOsSideEffects = async (context: BrowserContext): Promise<void> => {
    for (const pattern of OS_SIDE_EFFECT_ROUTES) {
        // Matches what the real endpoint returns on success.
        await context.route(pattern, (route) => route.fulfill({status: 202, body: ""}));
    }
};
