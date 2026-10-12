import {expect, type Page} from "@playwright/test";
import {defineWalkthrough} from "../../_support";

/**
 * Photo services walkthrough — docs/guide/create-customize/photo-services.md
 *
 * The docs sandbox has no provider key, so shots stop at the states reachable
 * without the builder model: the New automation choice and an empty builder.
 */
const SHOT_SERVICE_NAME = "Flickr exports";
let temporaryServicePath: string | undefined;

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
                await chooseNewService(page);
                await page.mouse.move(1390, 0);
            },
        },
        "service-builder.webp": {
            viewport: {width: 1400, height: 1000},
            goto: "/utilities/automations",
            clip: ".utilities-content",
            setup: async (page) => {
                try {
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
            teardown: removeTemporaryService,
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
