import {expect, type Page} from "@playwright/test";
import {defineWalkthrough} from "../../_support";

/**
 * Photo services walkthrough — docs/guide/create-customize/photo-services.md
 *
 * The docs sandbox has no provider key, so shots stop at the states reachable
 * without the builder model: the New automation choice and an empty builder.
 */
const SHOT_SERVICE_NAME = "Flickr exports";
const PAGES_BAR_ENDPOINT = "/pages/bar-visibility";

let temporaryServicePath: string | undefined;
let pagesBarWasExpanded = false;

const removeTemporaryService = async (page: Page): Promise<void> => {
    if (!temporaryServicePath) return;
    const path = temporaryServicePath;
    temporaryServicePath = undefined;
    const token = await page.evaluate(() => (
        window as Window & {APP_CONFIG?: {csrfToken?: string}}
    ).APP_CONFIG?.csrfToken).catch(() => undefined);
    const response = await page.request.post(`${path}/delete`, {
        headers: token ? {"X-CSRF-Token": token} : {},
    });
    if (!response.ok() && response.status() !== 404) {
        throw new Error(`Could not remove the documentation service: HTTP ${response.status()}`);
    }
};

/**
 * The Pages strip is an app-wide preference behind the navbar, saved through
 * `PUT /pages/bar-visibility` and collapsed unless it has been switched on. It
 * sets the navbar's height, so it also moves the page content that shows in the
 * margin these captures keep around the element they clip. Both shots here show
 * the collapsed strip the app ships with, so pin that state, and hand the
 * sandbox back what we found once the shot is taken — otherwise a walkthrough
 * that expands the strip (custom pages need it) silently re-frames them.
 * Uses the strip's own toggle, which applies the change to the DOM at once and
 * carries the CSRF token the route requires.
 */
const pinPagesBar = async (page: Page, expanded: boolean): Promise<void> => {
    const toggle = page.locator("#nav-pages-toggle");
    if (((await toggle.getAttribute("aria-expanded")) === "true") === expanded) return;
    // Awaited together: if the click fails, nothing is left waiting on the
    // response to reject later, unhandled, after the capture has moved on.
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

/**
 * Cleanup, so a direct request rather than the toggle: the shot may leave a modal
 * over the navbar, and the page closes straight after, so nothing needs the DOM to
 * follow. The route takes the same CSRF token the toggle sends.
 */
const restorePagesBar = async (page: Page): Promise<void> => {
    if (!pagesBarWasExpanded) return;
    pagesBarWasExpanded = false;
    const token = await page.evaluate(() => (
        window as Window & {APP_CONFIG?: {csrfToken?: string}}
    ).APP_CONFIG?.csrfToken).catch(() => undefined);
    const response = await page.request.put(PAGES_BAR_ENDPOINT, {
        data: {visible: true},
        headers: token ? {"X-CSRF-Token": token} : {},
    });
    if (!response.ok()) {
        throw new Error(`Could not restore the Pages strip state: HTTP ${response.status()}`);
    }
};

const chooseNewService = async (page: Page): Promise<void> => {
    await page.locator("#new-automation-button").click();
    const modal = page.locator("#newAutomationModal.active");
    await modal.locator('input[name="kind"][value="service"]').check();
    await modal.locator("#new-automation-name").fill(SHOT_SERVICE_NAME);
};

export default defineWalkthrough({
    page: "create-customize/photo-services",
    shots: {
        "new-photo-service.webp": {
            viewport: {width: 1400, height: 1000},
            goto: "/utilities/automations",
            clip: "#newAutomationModal .modal-content",
            setup: async (page) => {
                await collapsePagesBar(page);
                await chooseNewService(page);
                await page.mouse.move(1390, 0);
            },
            teardown: restorePagesBar,
        },
        "service-builder.webp": {
            viewport: {width: 1400, height: 1000},
            goto: "/utilities/automations",
            clip: ".utilities-content",
            setup: async (page) => {
                try {
                    await collapsePagesBar(page);
                    await chooseNewService(page);
                    await Promise.all([
                        page.waitForURL(/\/utilities\/automations\/services\/[^/?]+$/),
                        page.locator('#newAutomationModal.active button[type="submit"]').click(),
                    ]);
                    temporaryServicePath = new URL(page.url()).pathname;
                    await expect(page.locator(".service-tabs")).toBeVisible();
                    await page.mouse.move(1390, 0);
                } catch (error) {
                    await removeTemporaryService(page).catch(() => undefined);
                    throw error;
                }
            },
            teardown: async (page) => {
                await removeTemporaryService(page);
                await restorePagesBar(page);
            },
        },
    },
    flows: async ({page, visit}) => {
        let servicePath: string | undefined;
        try {
            await visit("/utilities/automations");
            await page.locator("#new-automation-button").click();
            const modal = page.locator("#newAutomationModal.active");
            await modal.locator('input[name="kind"][value="service"]').check();
            const name = `Documentation Photo Service ${Date.now()}`;
            await modal.locator("#new-automation-name").fill(name);
            await Promise.all([
                page.waitForURL(/\/utilities\/automations\/services\/[^/?]+$/),
                modal.locator('button[type="submit"]').click(),
            ]);
            servicePath = new URL(page.url()).pathname;
            await expect(page.locator(".page-header")).toContainText(name);

            const tabs = [
                ["Overview", "Nothing yet. The builder fills this in as it writes the service."],
                ["Account", "Appears once the builder knows how this service signs in."],
                ["Access", "No web calls yet."],
                ["Code", "No code yet."],
                ["Test", "Nothing to test yet."],
            ];
            for (const [label, message] of tabs) {
                await page.locator(".service-tabs").getByRole("link", {name: label, exact: true}).click();
                await expect(page.locator('.service-tabs [aria-current="page"]')).toHaveText(label);
                await expect(page.getByText(message, {exact: true})).toBeVisible();
            }

            // Each tab is a full page load. Delete asks for confirmation only once the
            // page script has attached its handler; clicked before that, the form
            // submits natively and the dialog never opens.
            await page.locator("html[data-app-ready]").waitFor({state: "attached"});
            await page.getByRole("button", {name: "Delete", exact: true}).click();
            await page.locator("#global-confirm-dialog.active").waitFor();
            // /utilities/automations redirects to the first automation, so wait to
            // leave the service rather than for one landing URL.
            const deletedPath = servicePath;
            await Promise.all([
                page.waitForURL((url) => url.pathname !== deletedPath),
                page.locator("#confirm-dialog-confirm").click(),
            ]);
            servicePath = undefined;
        } finally {
            if (servicePath) {
                const token = await page.evaluate(() => (
                    window as Window & {APP_CONFIG?: {csrfToken?: string}}
                ).APP_CONFIG?.csrfToken);
                const response = await page.request.post(`${servicePath}/delete`, {
                    headers: token ? {"X-CSRF-Token": token} : {},
                });
                if (!response.ok() && response.status() !== 404) {
                    throw new Error(`Could not remove the documentation service: HTTP ${response.status()}`);
                }
            }
        }
    },
});
