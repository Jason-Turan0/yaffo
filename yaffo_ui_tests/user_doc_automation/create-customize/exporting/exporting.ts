import {expect, type Page} from "@playwright/test";
import {defineWalkthrough, docsFixturePath} from "../../_support";

const SYNC_NAME = "Documentation Export Walkthrough";
const TARGET_FOLDER = docsFixturePath("Duplicate Scan Samples");
let temporarySyncPath: string | undefined;

const waitForAppReady = async (page: Page): Promise<void> => {
    await page.waitForFunction(() => Boolean(
        (window as Window & {PHOTO_ORGANIZER?: {appReady?: Promise<unknown>}})
            .PHOTO_ORGANIZER?.appReady
    ));
    await page.evaluate(async () => {
        const app = (window as Window & {PHOTO_ORGANIZER?: {appReady?: Promise<unknown>}})
            .PHOTO_ORGANIZER;
        if (!app?.appReady) throw new Error("Yaffo app initialization did not complete");
        await app.appReady;
    });
};

const createTargetSync = async (page: Page, service = "Folder"): Promise<void> => {
    await page.locator("#new-automation-button").click();
    const modal = page.locator("#newAutomationModal.active");
    await modal.locator('input[name="kind"][value="send"]').check();
    await modal.locator(`[data-sync-services="send"] .choice-card:has(.choice-card-title:text-is("${service}")) input[name="adapter_id"]`)
        .check();
    await modal.locator("#new-automation-name").fill(SYNC_NAME);
    await Promise.all([
        page.waitForURL(/\/utilities\/automations\/[^/?]+$/, {waitUntil: "domcontentloaded"}),
        modal.locator('button[type="submit"]').click(),
    ]);
    await waitForAppReady(page);
    temporarySyncPath = new URL(page.url()).pathname;
    await expect(page.locator(".page-header")).toContainText(SYNC_NAME);
};

const configureDestination = async (page: Page): Promise<void> => {
    const options = page.locator("#sync-step-options");
    await options.locator("#sync-option-folder").fill(TARGET_FOLDER);
    await Promise.all([
        page.waitForResponse((response) => response.url().includes("/sync/options") &&
            response.request().method() === "POST"),
        options.locator('button[type="submit"]').click(),
    ]);
    await waitForAppReady(page);
    await expect(options).toContainText("Folder and options");
};

const chooseAlbum = async (page: Page): Promise<void> => {
    const selection = page.locator("#sync-step-where");
    await selection.locator("#sync-selection-album").check();
    await selection.locator("#sync-album").selectOption({label: "Seeded Album"}, {force: true});
    await Promise.all([
        page.waitForResponse((response) => response.url().includes("/sync/selection") &&
            response.request().method() === "POST"),
        selection.locator("[data-selection-submit]").click(),
    ]);
    await waitForAppReady(page);
    await expect(page.locator("#sync-test")).toBeVisible();
};

const removeTemporarySync = async (page: Page): Promise<void> => {
    if (!temporarySyncPath) return;
    const path = temporarySyncPath;
    temporarySyncPath = undefined;
    const token = await page.evaluate(() => (
        window as Window & {APP_CONFIG?: {csrfToken?: string}}
    ).APP_CONFIG?.csrfToken).catch(() => undefined);
    const response = await page.request.post(`${path}/delete`, {
        headers: token ? {"X-CSRF-Token": token} : {},
    });
    if (!response.ok()) {
        throw new Error(`Could not remove the documentation sync: HTTP ${response.status()}`);
    }
};

const withCleanup = async (page: Page,
                           setup: (page: Page) => Promise<void>): Promise<void> => {
    try {
        await setup(page);
    } catch (error) {
        await removeTemporarySync(page).catch(() => undefined);
        throw error;
    }
};

const teardown = async (page: Page): Promise<void> => {
    await removeTemporarySync(page);
};

const configureForPreview = async (page: Page): Promise<void> => {
    await createTargetSync(page);
    await configureDestination(page);
    await chooseAlbum(page);
};

const enableSync = async (page: Page): Promise<void> => {
    await Promise.all([
        page.waitForResponse((response) => response.url().includes("/sync/turn-on") &&
            response.request().method() === "POST"),
        page.locator('form[action$="/sync/turn-on"] button[role="switch"]').click(),
    ]);
    await waitForAppReady(page);
    await expect(page.locator("#sync-run-now")).toBeVisible();
};

export default defineWalkthrough({
    page: "create-customize/exporting",
    shots: {
        "new-export-sync.webp": {
            viewport: {width: 1400, height: 1000},
            goto: "/utilities/automations",
            clip: "#newAutomationModal .modal-content",
            setup: async (page) => withCleanup(page, async () => {
                await page.locator("#new-automation-button").click();
                const modal = page.locator("#newAutomationModal.active");
                await modal.locator('input[name="kind"][value="send"]').check();
                await modal.locator('[data-sync-services="send"] .choice-card:has(.choice-card-title:text-is("Folder")) input[name="adapter_id"]')
                    .check();
                await page.mouse.move(1390, 0);
            }),
        },
        "export-account.webp": {
            viewport: {width: 1400, height: 1200},
            goto: "/utilities/automations",
            clip: "#sync-step-account",
            setup: async (page) => withCleanup(page, async () => {
                await createTargetSync(page, "Immich");
                await expect(page.locator("#sync-step-account")).toBeVisible();
            }),
            teardown,
        },
        "export-folder-options.webp": {
            viewport: {width: 1400, height: 1500},
            goto: "/utilities/automations",
            clip: "#sync-step-options",
            setup: async (page) => withCleanup(page, async () => {
                await createTargetSync(page);
                await page.locator("#sync-option-folder").fill(TARGET_FOLDER);
            }),
            teardown,
        },
        "export-selection.webp": {
            viewport: {width: 1400, height: 1500},
            goto: "/utilities/automations",
            clip: "#sync-step-where",
            setup: async (page) => withCleanup(page, async () => {
                await createTargetSync(page);
                await configureDestination(page);
                await page.locator("#sync-selection-album").check();
                await page.locator("#sync-album").selectOption({label: "Seeded Album"}, {force: true});
            }),
            teardown,
        },
        "export-custom-rule.webp": {
            viewport: {width: 1400, height: 1100},
            goto: "/utilities/automations",
            clip: ".automation-edit-screen",
            setup: async (page) => withCleanup(page, async () => {
                await createTargetSync(page);
                await configureDestination(page);
                const selection = page.locator("#sync-step-where");
                await selection.locator("#sync-selection-script").check();
                await Promise.all([
                    page.waitForURL(/\/utilities\/automations\/[^/?]+\/edit$/, {waitUntil: "domcontentloaded"}),
                    selection.locator("[data-selection-submit]").click(),
                ]);
                await waitForAppReady(page);
                await expect(page.locator(".automation-edit-screen")).toBeVisible();
                await page.locator(".automation-edit-screen textarea").fill(
                    "Send the photos in Seeded Album to the family photo frame folder.");
                await page.mouse.move(1390, 0);
            }),
            teardown,
        },
        "export-preview.webp": {
            viewport: {width: 1400, height: 1500},
            goto: "/utilities/automations",
            clip: "#sync-test-card",
            ignoreRegions: ["#sync-test-meta", "#sync-test-result .pagination-container"],
            setup: async (page) => withCleanup(page, async () => {
                await configureForPreview(page);
                const previewResponse = page.waitForResponse((response) => response.url().includes("/sync/test") &&
                    response.request().method() === "POST");
                await page.locator("#sync-test").click();
                await previewResponse;
                await expect(page.locator("#sync-test-card")).toBeVisible();
                await expect(page.locator("#sync-test-result .sync-test-stats")).toBeVisible();
            }),
            teardown,
        },
        "export-ready-to-run.webp": {
            viewport: {width: 1400, height: 1100},
            goto: "/utilities/automations",
            clip: ".page-header",
            ignoreRegions: [".sync-header-since"],
            setup: async (page) => withCleanup(page, async () => {
                await configureForPreview(page);
                await enableSync(page);
            }),
            teardown,
        },
        "export-schedule.webp": {
            viewport: {width: 1400, height: 1300},
            goto: "/utilities/automations",
            clip: "#automation-trigger-panel",
            setup: async (page) => withCleanup(page, async () => {
                await configureForPreview(page);
                await enableSync(page);
                await page.locator("#automation-trigger-panel > summary").click();
                await expect(page.locator("#automation-triggers .automation-trigger-row").first()).toBeVisible();
                await page.mouse.move(1390, 0);
            }),
            teardown,
        },
        "export-run-history.webp": {
            viewport: {width: 1400, height: 900},
            goto: "/utilities/automations",
            clip: "#automation-runs",
            setup: async (page) => withCleanup(page, async () => {
                await configureForPreview(page);
                await enableSync(page);
                await expect(page.locator("#automation-runs")).toContainText("No runs yet.");
            }),
            teardown,
        },
    },
});
