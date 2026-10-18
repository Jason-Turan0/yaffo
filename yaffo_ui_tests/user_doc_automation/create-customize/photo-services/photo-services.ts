/**
 * Photo services walkthrough — docs/guide/create-customize/photo-services.md
 *
 * The docs fixture seeds one published service, "Flickr exports"
 * (`custom.flickr-exports`), with a newer unpublished draft on top of it: the
 * header reads Published, the Code tab says "Differs from the published
 * version.", and Publish is offered. That single row is every tab this page
 * shows, so the shots are reads of it rather than a build — the builder route
 * needs a provider key the sandbox does not have, so nothing here can generate
 * a draft, and a freshly created service would only show the empty states.
 *
 * Each tab is a separate page load keyed by `?tab=`, so pinning the tab in the
 * URL is enough to reach it; there is no other state in the URL to pin.
 */
import {expect, type Page} from "@playwright/test";
import {defineWalkthrough} from "../../_support";

/** The fixture service. Its slug is derived from the name at seed time. */
const SERVICE = "/utilities/automations/services/custom.flickr-exports";

const PAGES_BAR_ENDPOINT = "/pages/bar-visibility";

const csrfToken = async (page: Page): Promise<string | undefined> => page.evaluate(() => (
    window as Window & {APP_CONFIG?: {csrfToken?: string}}
).APP_CONFIG?.csrfToken).catch(() => undefined);

/**
 * The Pages strip is an app-wide, server-persisted preference and it sets the
 * navbar's height, so it decides how far down the page sits. Every shot here is
 * clipped to an element, which the strip cannot move, but the two dialog shots
 * show the page behind the dialog in the padding a modal clip keeps — pin the
 * strip the app ships with (collapsed) and hand back what we found.
 */
let pagesBarWasExpanded = false;

const pinPagesBar = async (page: Page, expanded: boolean): Promise<void> => {
    const toggle = page.locator("#nav-pages-toggle");
    if (((await toggle.getAttribute("aria-expanded")) === "true") === expanded) return;
    const [response] = await Promise.all([
        page.waitForResponse((candidate) => (
            candidate.url().includes(PAGES_BAR_ENDPOINT) && candidate.request().method() === "PUT"
        )),
        toggle.click(),
    ]);
    if (!response.ok()) {
        throw new Error(`Could not save the Pages strip state: HTTP ${response.status()}`);
    }
    await expect(toggle).toHaveAttribute("aria-expanded", String(expanded));
};

const collapsePagesBar = async (page: Page): Promise<void> => {
    pagesBarWasExpanded =
        (await page.locator("#nav-pages-toggle").getAttribute("aria-expanded")) === "true";
    await pinPagesBar(page, false);
};

/** Cleanup, so a request rather than the toggle: the shot leaves a modal over the
 * navbar and the page closes straight after, so nothing needs the DOM to follow. */
const restorePagesBar = async (page: Page): Promise<void> => {
    if (!pagesBarWasExpanded) return;
    pagesBarWasExpanded = false;
    const token = await csrfToken(page);
    const response = await page.request.put(PAGES_BAR_ENDPOINT, {
        data: {visible: true},
        headers: token ? {"X-CSRF-Token": token} : {},
    });
    if (!response.ok()) {
        throw new Error(`Could not restore the Pages strip state: HTTP ${response.status()}`);
    }
};

/**
 * The dialog's radio choices gate their service list through the page script: a
 * list is a `[data-sync-services]` group, `hidden` until its kind is chosen, and
 * the radios inside it stay disabled. Clicking the card is what checks the radio,
 * so drive the labels rather than the inputs.
 */
const openNewAutomation = async (page: Page, kind: "service" | "send") => {
    await page.locator("html[data-app-ready]").waitFor({state: "attached"});
    await page.locator("#new-automation-button").click();
    const modal = page.locator("#newAutomationModal.active");
    await modal.locator(`label.choice-card:has(input[value="${kind}"])`).click();
    return modal;
};

export default defineWalkthrough({
    page: "create-customize/photo-services",

    shots: {
        // "Choose Add a New Service" — the dialog as it looks just before Create.
        "new-photo-service.webp": {
            viewport: {width: 1400, height: 1000},
            goto: "/utilities/automations",
            clip: "#newAutomationModal .modal-content",
            // The dialog is the whole subject; padding would only add the page
            // behind it, which is a different page on every run.
            clipPadding: 0,
            setup: async (page) => {
                await collapsePagesBar(page);
                const modal = await openNewAutomation(page, "service");
                await modal.locator("#new-automation-name").fill("Flickr exports");
                // Blur the name field: the focus ring is a caret, not content.
                await page.mouse.move(1390, 0);
            },
            teardown: restorePagesBar,
        },

        // "Describe the Connection" — conversation on the left, the five tabs on
        // the right, over the seeded draft rather than an empty new one.
        "service-builder.webp": {
            viewport: {width: 1400, height: 1000},
            goto: `${SERVICE}?tab=overview`,
            clip: ".utilities-content",
        },

        // "Check the Overview" — the two halves again, so the panel is the subject.
        "service-overview.webp": {
            viewport: {width: 1400, height: 1000},
            goto: `${SERVICE}?tab=overview`,
            clip: ".service-panel",
        },

        // "Add App Credentials and a Test Account" — the callback URL, the two
        // credential fields, and the test-account button (disabled until the app
        // is saved, which is the state a reader starts in).
        "service-account.webp": {
            viewport: {width: 1400, height: 1000},
            goto: `${SERVICE}?tab=account`,
            clip: ".service-panel",
        },

        // "Read the Access List" — the three calls the static check found.
        "service-access.webp": {
            viewport: {width: 1400, height: 1000},
            goto: `${SERVICE}?tab=web`,
            clip: ".service-panel",
        },

        // "Inspect the Code" — the draft's code. `.automation-code` caps its own
        // height and scrolls, so the crop shows the thin-scrollbar window, not
        // the whole 200-line file.
        "service-code.webp": {
            viewport: {width: 1400, height: 1000},
            goto: `${SERVICE}?tab=code`,
            clip: ".service-panel",
        },

        // "Test the Draft" — the option fields and the Test button. No result:
        // a result needs a real signed-in account, so the page describes it in
        // prose instead of picturing it.
        "service-test.webp": {
            viewport: {width: 1400, height: 1000},
            goto: `${SERVICE}?tab=test`,
            clip: ".service-panel",
        },

        // "Publish" — the review modal. Publish opens it through the page script,
        // so it needs the app to be initialised; the panel is not part of the
        // modal, so it is not in the crop.
        "service-publish-review.webp": {
            viewport: {width: 1400, height: 1000},
            goto: `${SERVICE}?tab=web`,
            clip: "#servicePublishModal .modal-content",
            clipPadding: 0,
            setup: async (page) => {
                await collapsePagesBar(page);
                await page.locator("html[data-app-ready]").waitFor({state: "attached"});
                await page.locator("#service-publish").click();
                await page.locator("#servicePublishModal.active").waitFor({state: "visible"});
                await page.mouse.move(1390, 0);
            },
            teardown: restorePagesBar,
        },

        // "Create a Sync With It" — the published service offered under Send to.
        "new-sync-send-to.webp": {
            // Tall enough that the dialog body (capped at 60vh) holds the whole
            // service list; at 1000px the published service sat below the fold.
            viewport: {width: 1400, height: 1700},
            goto: "/utilities/automations",
            clip: "#newAutomationModal .modal-content",
            clipPadding: 0,
            setup: async (page) => {
                await collapsePagesBar(page);
                const modal = await openNewAutomation(page, "send");
                // Select the published service, so the shot shows what the step is
                // about rather than the default Folder choice.
                const flickr = modal.locator('[data-sync-services="send"] .choice-card')
                    .filter({hasText: "Flickr exports"});
                await flickr.scrollIntoViewIfNeeded();
                await flickr.click();
                await expect(flickr.locator("input")).toBeChecked();
                await page.mouse.move(1390, 0);
            },
            teardown: restorePagesBar,
        },
    },

    flows: async ({page, visit}) => {
        // "While the Builder Works" — a docs-domain answer goes through the same
        // page as the conversation. The seeded permission message is already
        // resolved, so answering it again must be refused rather than restart a
        // run; that 409 is the branch that cannot be clicked at fixture time.
        await visit(`${SERVICE}?tab=overview`);
        const token = await csrfToken(page);
        const answer = await page.request.post(`${SERVICE}/permissions/11`, {
            data: {decision: "allow", domains: ["www.flickr.com"]},
            headers: token ? {"X-CSRF-Token": token} : {},
        });
        expect([200, 409]).toContain(answer.status());

        // "Before You Start" — the chat route, driven directly. With no provider
        // key it answers 400 api_key_missing and stores nothing; with a key it
        // would answer 202. Either is a fixture fact, not a page defect.
        const chat = await page.request.post(`${SERVICE}/chat`, {
            data: {message: "Send selected photos to Flickr sets. Let me choose a destination set."},
            headers: {"Content-Type": "application/json", ...(token ? {"X-CSRF-Token": token} : {})},
        });
        expect([202, 400, 409]).toContain(chat.status());

        // "Add App Credentials and a Test Account" — saving credentials writes to
        // the keychain and flips the page to its Saved state, so it is not driven;
        // the disabled test-account button is, which is the App-credentials-first
        // ordering the tab enforces.
        await visit(`${SERVICE}?tab=account`);
        await expect(page.locator("button:has-text('Sign in to')").first()).toBeDisabled();

        // "Update or Remove the Service" — Regenerate only renders when the
        // published code fails the current contract's checks, so it is absent on
        // a healthy fixture; Delete is present but disabled while a sync uses the
        // service, which the fixture leaves at zero users. Assert the enabled
        // state and leave the service alone: deleting it would empty every later
        // run's shots.
        await expect(page.getByRole("button", {name: "Regenerate"})).toHaveCount(0);
        await expect(page.getByRole("button", {name: "Delete", exact: true})).toBeEnabled();

        // "Create a Sync With It" — the send list is a real, enabled group once
        // its kind is chosen; the radios stay disabled only until the script runs.
        await visit("/utilities/automations");
        await page.locator("html[data-app-ready]").waitFor({state: "attached"});
        await page.locator("#new-automation-button").click();
        const modal = page.locator("#newAutomationModal.active");
        await modal.locator('label.choice-card:has(input[value="send"])').click();
        await expect(modal.locator('[data-sync-services="send"]')).toBeVisible();
        await expect(modal.locator('[data-sync-services="send"]'))
            .toContainText("Flickr exports");
        await page.keyboard.press("Escape");
    },
});
