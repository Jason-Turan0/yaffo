import {expect, type Page} from "@playwright/test";
import {defineWalkthrough, docsFixturePath} from "../../_support";

/**
 * Importing walkthrough — docs/guide/create-customize/importing.md
 *
 * Every shot builds a disposable Folder import and removes it in teardown. The
 * source holds copies of library photos outside the library, so Test shows Yaffo
 * recognizing existing photos without copying anything; no shot turns the sync on.
 */
const SYNC_NAME = "Documentation Import Walkthrough";
// Import sources must be outside the library. These are copies of library photos, so
// Test shows Yaffo recognizing photos it already has instead of copying them again.
const SOURCE_FOLDER = docsFixturePath("Duplicate Scan Samples");
const IMPORT_SUBFOLDER = "Imported";
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

const chooseFolderImport = async (page: Page): Promise<void> => {
    await page.locator("#new-automation-button").click();
    const modal = page.locator("#newAutomationModal.active");
    await modal.locator('input[name="kind"][value="import"]').check();
    await modal.locator('[data-sync-services="import"] .choice-card:has(.choice-card-title:text-is("Folder")) input[name="adapter_id"]')
        .check();
};

const createSourceSync = async (page: Page): Promise<void> => {
    await chooseFolderImport(page);
    const modal = page.locator("#newAutomationModal.active");
    await modal.locator("#new-automation-name").fill(SYNC_NAME);
    await Promise.all([
        page.waitForURL(/\/utilities\/automations\/[^/?]+$/, {waitUntil: "domcontentloaded"}),
        modal.locator('button[type="submit"]').click(),
    ]);
    await waitForAppReady(page);
    temporarySyncPath = new URL(page.url()).pathname;
    await expect(page.locator(".page-header")).toContainText(SYNC_NAME);
};

const configureSource = async (page: Page): Promise<void> => {
    const options = page.locator("#sync-step-options");
    await options.locator("#sync-option-folder").fill(SOURCE_FOLDER);
    await Promise.all([
        page.waitForResponse((response) => response.url().includes("/sync/options") &&
            response.request().method() === "POST"),
        options.locator('button[type="submit"]').click(),
    ]);
    await waitForAppReady(page);
};

const fillDestination = async (page: Page): Promise<void> => {
    const where = page.locator("#sync-step-where");
    if (!(await where.evaluate((element) => (element as HTMLDetailsElement).open))) {
        await where.locator("> summary").click();
    }
    await where.locator("#sync-subpath").fill(IMPORT_SUBFOLDER);
    await where.locator("#sync-layout").selectOption("year_month", {force: true});
};

const configureDestination = async (page: Page): Promise<void> => {
    await fillDestination(page);
    await Promise.all([
        page.waitForResponse((response) => response.url().includes("/sync/import-root") &&
            response.request().method() === "POST"),
        page.locator('#sync-step-where button[type="submit"]').click(),
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

export default defineWalkthrough({
    page: "create-customize/importing",
    shots: {
        "new-import-sync.webp": {
            viewport: {width: 1400, height: 1000},
            goto: "/utilities/automations",
            clip: "#newAutomationModal .modal-content",
            setup: async (page) => {
                await chooseFolderImport(page);
                await page.locator("#newAutomationModal.active #new-automation-name").fill("Camera card");
                await page.mouse.move(1390, 0);
            },
        },
        "import-folder-options.webp": {
            viewport: {width: 1400, height: 1400},
            goto: "/utilities/automations",
            clip: "#sync-step-options",
            setup: async (page) => withCleanup(page, async () => {
                await createSourceSync(page);
                await page.locator("#sync-option-folder").fill(SOURCE_FOLDER);
                await page.mouse.move(1390, 0);
            }),
            teardown,
        },
        "import-destination.webp": {
            viewport: {width: 1400, height: 1400},
            goto: "/utilities/automations",
            clip: "#sync-step-where",
            setup: async (page) => withCleanup(page, async () => {
                await createSourceSync(page);
                await configureSource(page);
                await fillDestination(page);
                await page.mouse.move(1390, 0);
            }),
            teardown,
        },
        "import-preview.webp": {
            viewport: {width: 1400, height: 1500},
            goto: "/utilities/automations",
            clip: "#sync-test-card",
            ignoreRegions: ["#sync-test-meta", "#sync-test-result .pagination-container"],
            setup: async (page) => withCleanup(page, async () => {
                await createSourceSync(page);
                await configureSource(page);
                await configureDestination(page);
                const previewResponse = page.waitForResponse((response) => response.url().includes("/sync/test") &&
                    response.request().method() === "POST");
                await page.locator("#sync-test").click();
                await previewResponse;
                await expect(page.locator("#sync-test-result .sync-test-stats")).toBeVisible();
                await page.mouse.move(1390, 0);
            }),
            teardown,
        },
    },
    flows: async ({page, visit}) => {
        try {
            await visit("/utilities/automations");
            await waitForAppReady(page);
            await createSourceSync(page);
            await expect(page.locator("#sync-step-options")).toContainText("Folder and options");
            await expect(page.locator("#sync-step-where")).toContainText("Where to put them");
            await expect(page.locator("#sync-run-now")).toHaveCount(0);
            await configureSource(page);
            await configureDestination(page);

            // A new sync starts with a nightly schedule but stays off until reviewed.
            await page.locator("#automation-trigger-panel > summary").click();
            await expect(page.locator("#automation-triggers .automation-trigger-row").first()).toBeVisible();
            await expect(page.locator("#automation-runs")).toContainText("No runs yet");

            await page.locator("#delete-automation-button").click();
            await page.locator("#global-confirm-dialog.active").waitFor();
            const syncPath = temporarySyncPath;
            await Promise.all([
                page.waitForURL(url => url.pathname !== syncPath),
                page.locator("#confirm-dialog-confirm").click(),
            ]);
            temporarySyncPath = undefined;
        } finally {
            await removeTemporarySync(page);
        }
    },
});
