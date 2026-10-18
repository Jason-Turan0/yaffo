/**
 * Custom pages walkthrough — docs/guide/create-customize/custom-pages.md
 *
 * The page describes a build the docs sandbox cannot perform: page generation needs
 * a provider key the sandbox does not have, so nothing here sends a chat message
 * that would start a real run. Instead the guide's Assistant walkthrough is
 * illustrated from the seeded states the fixture keeps for it:
 *
 *   "Florida Trip"           published, hero + photo gallery (the presentation and
 *                            design-view shots);
 *   "Maya's Third Birthday"  a READY draft that adds a People widget below its
 *                            party-photo grid and waits for Save (review, save);
 *   "Chicago 2015"           still generating, its working version started at
 *                            GENERATION_STARTED_AT (the locked-canvas step).
 *
 * Those three are read-only here. Save, Send, Cancel and Delete on a seeded page are
 * never clicked — they write to the shared fixture. Every write in this walkthrough
 * is on a page it creates first, and that page is deleted again in teardown.
 */
import type {Locator, Page} from "@playwright/test";
import {defineWalkthrough} from "../../_support";

/** Any page carries the Pages strip, so it is a neutral place to start from. */
const START = "/settings";

const FLORIDA = "Florida Trip";
const MAYA = "Maya's Third Birthday";
const CHICAGO = "Chicago 2015";

const TEMP_TITLE = "Documentation Example";
const TEMP_DESCRIPTION =
    "A page for our Siesta Key trip: a hero photo across the top, then the rest " +
    "of the beach photos in a grid below it.";

/**
 * Chicago 2015's working version started here, so the elapsed timer is
 * `now - 2026-09-01T12:00:00Z`. Left alone it reads tens of thousands of minutes,
 * which is both wrong-looking and different on every run.
 */
const GENERATION_STARTED_AT = "2026-09-01T12:00:00Z";
/** The frozen instant, 2:41 after the run started. */
const FROZEN_NOW = "2026-09-01T12:02:41Z";

/** The page this walkthrough created, so teardown can delete it. */
let temporaryPageId: number | undefined;
/** Whether the Pages strip was already open when this run first touched it. */
let stripWasExpanded: boolean | null = null;

/**
 * A widget's frame is thrown away and rebuilt whenever the design page reconciles a
 * polled change (every 1.5s while a generation is running), which detaches any
 * locator pointing inside it — `locator.evaluate` then fails with "Frame was
 * detached". Every wait below goes through this so a detach re-resolves the locator
 * against the replacement frame instead of failing the capture.
 */
const withDetachRetry = async (step: () => Promise<void>, attempts = 6): Promise<void> => {
    for (let attempt = 1; ; attempt += 1) {
        try {
            await step();
            return;
        } catch (error) {
            if (attempt >= attempts) throw error;
            await new Promise((resolve) => setTimeout(resolve, 250));
        }
    }
};

const waitForImage = async (image: Locator): Promise<void> => withDetachRetry(async () => {
    await image.waitFor();
    await image.evaluate((node: HTMLImageElement) => {
        if (node.complete) return;
        return new Promise<void>((resolve) => {
            node.addEventListener("load", () => resolve(), {once: true});
            node.addEventListener("error", () => resolve(), {once: true});
        });
    });
});

/**
 * The Pages strip is an app-wide, server-persisted preference: every tab link, the
 * edit pencil and + New page live inside it, and nothing on the page can reach them
 * while it is folded away. Drive the toggle rather than reading the tabs out of the
 * sidebar, and remember what state we found so teardown can put it back.
 */
const setStripExpanded = async (page: Page, expanded: boolean): Promise<void> => {
    const toggle = page.locator("#nav-pages-toggle");
    await toggle.waitFor({state: "attached"});
    const current = (await toggle.getAttribute("aria-expanded")) === "true";
    if (current === expanded) return;
    const [response] = await Promise.all([
        page.waitForResponse((candidate) => (
            candidate.url().includes("/pages/bar-visibility") &&
            candidate.request().method() === "PUT"
        )),
        toggle.click(),
    ]);
    if (!response.ok()) {
        throw new Error(`Could not save the Pages strip state: HTTP ${response.status()}`);
    }
};

const openStrip = async (page: Page): Promise<void> => {
    if (stripWasExpanded === null) {
        stripWasExpanded = (await page.locator("#nav-pages-toggle")
            .getAttribute("aria-expanded")) === "true";
    }
    await setStripExpanded(page, true);
    await page.locator("#navbar-pages-bar").waitFor({state: "visible"});
};

const restoreStrip = async (page: Page): Promise<void> => {
    const expanded = stripWasExpanded;
    stripWasExpanded = null;
    if (expanded !== false) return;  // we found it open, or never touched it
    await setStripExpanded(page, false).catch(() => undefined);
};

/**
 * A seeded page's URL, read from its strip tab. Page ids come from the seed, but the
 * tab is the app's own record of them, so nothing here hard-codes /pages/1.
 */
const pagePath = async (page: Page, title: string): Promise<string> => {
    await openStrip(page);
    const href = await page.locator(".nav-page-tab")
        .filter({hasText: title})
        .first()
        .getAttribute("href");
    if (!href?.match(/^\/pages\/\d+$/)) {
        throw new Error(`Could not find a tab for the seeded page ${title}`);
    }
    return href;
};

const openDesign = async (page: Page, title: string): Promise<void> => {
    const path = await pagePath(page, title);
    await page.goto(`${path}/design`, {waitUntil: "domcontentloaded"});
    await page.locator(".page-design").waitFor();
};

const openPresentation = async (page: Page, title: string): Promise<void> => {
    const path = await pagePath(page, title);
    await page.goto(path, {waitUntil: "domcontentloaded"});
    await page.locator(".page-presentation").waitFor();
};

/** Florida Trip: a hero banner over a thirteen-photo gallery. */
const waitForFlorida = async (page: Page): Promise<void> => {
    const hero = page.frameLocator('iframe[title="Hero banner preview"]');
    await hero.locator("#hero-title").filter({hasText: "Florida"}).waitFor();
    await waitForImage(hero.locator("#hero-img"));
    const last = page.frameLocator('iframe[title="Photo gallery preview"]')
        .locator(".gallery-item")
        .nth(12);
    await withDetachRetry(() => last.waitFor());
    await waitForImage(last.locator("img"));
};

/** Maya's draft: the party-photo grid plus the People widget the second message added. */
const waitForMayaDraft = async (page: Page): Promise<void> => {
    const grid = page.frameLocator('iframe[title="Party photos preview"]').locator("img");
    await withDetachRetry(() => grid.nth(3).waitFor());
    await waitForImage(grid.nth(3));
    await page.frameLocator('iframe[title="People preview"]').locator("#pills > *").first().waitFor();
};

/**
 * A page whose version is READY still enters the running phase for its first poll,
 * so the design view briefly carries `is-generating`. Wait for the poll to settle
 * before capturing anything that is meant to look idle.
 */
const waitForReview = async (page: Page): Promise<void> => {
    await page.waitForFunction(() => (
        !document.querySelector(".page-design")?.classList.contains("is-generating")
    ));
    await page.locator("#conversation-status").waitFor({state: "hidden"});
};

/**
 * Click + New page and land on the new page's design view. Creation posts to /pages;
 * the server lands on /pages/<id> and only forwards to /design while the page has no
 * widgets, so pin the design URL rather than depend on that hop.
 */
const createTemporaryPage = async (page: Page): Promise<number> => {
    await openStrip(page);
    const button = page.locator(".nav-new-page");
    await button.waitFor();
    await Promise.all([
        page.waitForURL(/\/pages\/\d+(\/design)?$/, {waitUntil: "domcontentloaded"}),
        button.click(),
    ]);
    const pageId = Number(page.url().match(/\/pages\/(\d+)/)?.[1]);
    if (!pageId) throw new Error("The new custom page has no id");
    if (!/\/design$/.test(new URL(page.url()).pathname)) {
        await page.goto(`/pages/${pageId}/design`, {waitUntil: "domcontentloaded"});
    }
    await page.locator(".page-design").waitFor();
    temporaryPageId = pageId;
    return pageId;
};

/** Cleanup, so a request rather than the visible dialog: teardown runs after the
 * shot, when nothing needs the DOM to follow. */
const deletePageById = async (page: Page, pageId: number): Promise<void> => {
    const token = await page.evaluate(() => (
        window as Window & {APP_CONFIG?: {csrfToken?: string}}
    ).APP_CONFIG?.csrfToken);
    if (!token) throw new Error("Could not read a CSRF token for page cleanup");
    const response = await page.request.post(`/pages/${pageId}/delete`, {
        form: {csrf_token: token},
    });
    if (!response.ok() && response.status() !== 404) {
        throw new Error(`Could not delete custom page ${pageId}: HTTP ${response.status()}`);
    }
};

const deleteTemporaryPage = async (page: Page): Promise<void> => {
    const pageId = temporaryPageId;
    temporaryPageId = undefined;
    if (!pageId) return;
    await deletePageById(page, pageId);
};

/** Add a blank widget and name it, leaving the design view as it is. */
const addNamedWidget = async (page: Page, title: string): Promise<void> => {
    await page.locator("#add-widget-button").click();
    const widget = page.locator(".grid-stack .grid-stack-item").first();
    await widget.waitFor();
    await widget.locator(".widget-edit").click();
    await widget.locator(".widget-title-input").fill(title);
    await widget.locator(".widget-title-input").press("Enter");
};

export default defineWalkthrough({
    page: "create-customize/custom-pages",

    shots: {
        // "Custom Pages" — the published page the intro describes.
        "custom-page-view.webp": {
            annotations: {
                "hero-widget": ".page-presentation .grid-stack-item >> nth=0",
                "gallery-widget": ".page-presentation .grid-stack-item >> nth=1",
            },
            viewport: {width: 1400, height: 1500},
            goto: START,
            clip: ".page-presentation",
            setup: async (page) => {
                await openPresentation(page, FLORIDA);
                await waitForFlorida(page);
                await page.mouse.move(0, 0);
            },
            teardown: restoreStrip,
        },

        // "Explore the Design View" — the same page in the editor.
        "custom-page-design.webp": {
            annotations: {
                "page-settings": ".editor-panel .editor-card",
                "assistant": ".editor-panel .chat-card",
                "widget-controls": ".editor-canvas .widget-header >> nth=0",
                "canvas": ".editor-canvas .grid-stack",
            },
            viewport: {width: 1400, height: 1200},
            goto: START,
            clip: ".page-design",
            setup: async (page) => {
                await openDesign(page, FLORIDA);
                await waitForFlorida(page);
                await page.locator("#conversation-messages .chat-message").nth(3).waitFor();
                await page.mouse.move(0, 0);
            },
            teardown: restoreStrip,
        },

        // Walkthrough step 1 — + New page and the empty design view it opens. No
        // clip: the strip the reader just clicked and the blank canvas are both the
        // subject, and no single element contains the two together.
        "new-page-empty.webp": {
            viewport: {width: 1400, height: 900},
            goto: START,
            setup: async (page) => {
                await createTemporaryPage(page);
                await page.mouse.move(0, 0);
            },
            teardown: async (page) => {
                await deleteTemporaryPage(page);
                await restoreStrip(page);
            },
        },

        // Walkthrough step 2 — the description typed but not yet sent. Sending would
        // post to the chat route, and the sandbox has no provider key.
        "assistant-description.webp": {
            viewport: {width: 1400, height: 1000},
            goto: START,
            clip: ".editor-panel .chat-card",
            setup: async (page) => {
                await createTemporaryPage(page);
                const box = page.locator("#conversation-message");
                await box.fill(TEMP_DESCRIPTION);
                // Drop focus so the shot is not framed around a caret.
                await box.blur();
                await page.mouse.move(0, 0);
            },
            teardown: async (page) => {
                await deleteTemporaryPage(page);
                await restoreStrip(page);
            },
        },

        // Walkthrough step 3 — the locked canvas. Chicago 2015 is the seeded page
        // that is still generating, so the clock is frozen two minutes and forty-one
        // seconds after its working version started; `setFixedTime` keeps the poll
        // and the one-second tick running while Date.now() stays put.
        "generation-running.webp": {
            viewport: {width: 1400, height: 1200},
            goto: START,
            clip: ".page-design",
            setup: async (page) => {
                await page.clock.setFixedTime(new Date(FROZEN_NOW));
                await openDesign(page, CHICAGO);
                await page.locator("#conversation-status").waitFor();
                // This page keeps polling while it is open, and each reconcile
                // rebuilds the widget frames, so the gallery is best-effort: a
                // detach here must not fail the capture. The grid item itself is in
                // the host page and is what the shot actually needs.
                await page.locator(".grid-stack .grid-stack-item").first().waitFor();
                await withDetachRetry(async () => {
                    await page.frameLocator('iframe[title="Photo gallery preview"]')
                        .locator(".gallery-item img").first().waitFor();
                }).catch(() => undefined);
                // The elapsed label is written on the one-second tick, not the first
                // paint; two real seconds covers it (the clock is fixed, the timers
                // are not).
                await page.waitForTimeout(2200);
                await page.mouse.move(0, 0);
            },
            teardown: restoreStrip,
        },

        // Walkthrough step 4 — a finished draft in review, before Save.
        "draft-review.webp": {
            viewport: {width: 1400, height: 1200},
            goto: START,
            clip: ".page-design",
            setup: async (page) => {
                await openDesign(page, MAYA);
                await waitForReview(page);
                await waitForMayaDraft(page);
                await page.mouse.move(0, 0);
            },
            teardown: restoreStrip,
        },

        // Walkthrough step 5 — the moment before Save, on the ready draft: the
        // editor panel with Save under the pointer. Never clicked: Save publishes
        // the seeded draft. `flows` performs a Save on a page it creates instead.
        "save-page.webp": {
            viewport: {width: 1400, height: 1200},
            goto: START,
            clip: ".page-design .editor-panel",
            setup: async (page) => {
                await openDesign(page, MAYA);
                await waitForReview(page);
                await waitForMayaDraft(page);
                await page.locator("#save-page-button").hover();
            },
            teardown: restoreStrip,
        },

        // Walkthrough step 6 — presentation mode. Clipped to the grid rather than the
        // whole presentation area: the point of the step is the widgets with no
        // editing controls around them.
        "page-presentation.webp": {
            viewport: {width: 1400, height: 1500},
            goto: START,
            clip: ".page-presentation .grid-stack",
            setup: async (page) => {
                await openPresentation(page, FLORIDA);
                await waitForFlorida(page);
                await page.mouse.move(0, 0);
            },
            teardown: restoreStrip,
        },
    },

    flows: async ({page}) => {
        try {
            await page.goto(START, {waitUntil: "domcontentloaded"});

            // "Create a Page" / "Work with Widgets" — the manual path from the strip
            // to a widget on the canvas. Every write lands on the page we just made.
            const pageId = await createTemporaryPage(page);
            await page.locator("#page-title").fill(TEMP_TITLE);
            await page.locator("#page-subtitle").fill("A temporary page used to check the editor");
            await addNamedWidget(page, "Notes");

            // "Save the Page" — Save commits the page and opens it in presentation
            // mode, which is also the only way a page with widgets gets there.
            await Promise.all([
                page.waitForURL(new RegExp(`/pages/${pageId}$`), {waitUntil: "domcontentloaded"}),
                page.locator("#save-page-button").click(),
            ]);
            await page.locator(".page-presentation").waitFor();

            // "Present and Edit a Page" — the pencil beside the active tab.
            await openStrip(page);
            await Promise.all([
                page.waitForURL(new RegExp(`/pages/${pageId}/design$`), {
                    waitUntil: "domcontentloaded",
                }),
                page.locator(".nav-page-edit").click(),
            ]);
            await page.locator(".page-design").waitFor();

            // "Work with Widgets" — × removes a widget after the shared confirmation
            // dialog, on the version being edited.
            const widget = page.locator(".grid-stack .grid-stack-item").first();
            await widget.locator(".widget-delete").click();
            await page.locator("#global-confirm-dialog.active").waitFor();
            await page.locator("#confirm-dialog-confirm").click();
            await page.locator(".grid-stack .grid-stack-item").waitFor({state: "detached"});

            // "Delete a Page" — the confirmation names the page, then the tab goes.
            await page.locator("#delete-page-button").click();
            await page.locator("#global-confirm-dialog.active").waitFor();
            await Promise.all([
                page.waitForURL(/\/$/, {waitUntil: "domcontentloaded"}),
                page.locator("#confirm-dialog-confirm").click(),
            ]);
            await page.locator(`nav a[href="/pages/${pageId}"]`).waitFor({state: "detached"});
            temporaryPageId = undefined;

            // "Generate and Refine a Page" — the seeded run in flight, read only. The
            // Cancel button is left alone: cancelling deletes the fixture's working
            // version.
            await openDesign(page, CHICAGO);
            await page.locator("#conversation-status").waitFor();

            // The READY draft, also read only — Save on this page would publish it.
            await openDesign(page, MAYA);
            await waitForReview(page);
        } finally {
            await deleteTemporaryPage(page).catch(() => undefined);
            await restoreStrip(page).catch(() => undefined);
        }
    },
});
