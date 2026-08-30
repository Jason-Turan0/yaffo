/**
 * Automations walkthrough — docs/guide/create-customize/automations.md
 *
 * Published shots use the reviewed File favorite kid photos fixture. The flow
 * creates one temporary custom automation, exercises its editable lifecycle,
 * and always removes it so the documentation fixture remains unchanged.
 */
import type {Locator, Page} from "@playwright/test";
import {defineWalkthrough} from "../../_support";

const SEEDED_SLUG = "file-favorite-kid-photos";
const SEEDED_NAME = "File favorite kid photos";
const TEMP_PREFIX = "Documentation Automation";
const TEMP_NAME = `${TEMP_PREFIX} Example`;
const RENAMED_NAME = `${TEMP_PREFIX} Arrival Filing`;

const automationsSidebar = (page: Page): Locator =>
    page.locator("nav.utilities-sidebar").filter({
        has: page.locator("h2", {hasText: "Automations"}),
    });

const customAutomationsNav = (page: Page): Locator =>
    automationsSidebar(page).locator('h3:has-text("Custom") + ul.panel-nav');

const systemAutomationsNav = (page: Page): Locator =>
    automationsSidebar(page).locator('h3:has-text("System") + ul.panel-nav');

const waitForAppReady = async (page: Page): Promise<void> => {
    await page.evaluate(async () => {
        const app = (window as typeof window & {
            PHOTO_ORGANIZER?: {appReady?: Promise<unknown>};
        }).PHOTO_ORGANIZER;
        if (!app?.appReady) throw new Error("Yaffo did not expose appReady");
        await app.appReady;
    });
};

const deleteAutomation = async (page: Page, slug: string): Promise<void> => {
    const response = await page.request.post(`/utilities/automations/${slug}/delete`);
    if (!response.ok() && response.status() !== 404) {
        throw new Error(`Could not clean up automation ${slug}: HTTP ${response.status()}`);
    }
};

const removeDocumentationAutomations = async (page: Page): Promise<void> => {
    const links = customAutomationsNav(page).locator("a");
    const count = await links.count();
    const slugs: string[] = [];
    for (let index = 0; index < count; index += 1) {
        const link = links.nth(index);
        const label = (await link.locator(".panel-nav-label").textContent())?.trim() ?? "";
        const href = await link.getAttribute("href");
        if (label.startsWith(TEMP_PREFIX) && href?.startsWith("/utilities/automations/")) {
            slugs.push(href.slice("/utilities/automations/".length));
        }
    }
    for (const slug of slugs) await deleteAutomation(page, slug);
    if (slugs.length > 0) await page.reload({waitUntil: "domcontentloaded"});
};

const openSeededAutomation = async (page: Page, suffix = ""): Promise<void> => {
    await page.goto(`/utilities/automations/${SEEDED_SLUG}${suffix}`, {
        waitUntil: "domcontentloaded",
    });
    await page.locator(".utilities-container").waitFor();
    await waitForAppReady(page);
};

const pickCurrentFolder = async (page: Page): Promise<void> => {
    const picker = page.locator("#folder-picker-modal");
    await picker.waitFor();
    await picker.locator("#folder-picker-path").filter({hasNotText: /^\s*$/}).waitFor();
    await picker.locator("#folder-picker-select").click();
    await picker.waitFor({state: "hidden"});
};

const showDryRun = async (page: Page): Promise<void> => {
    await page.locator("#automation-test-button").click();
    await pickCurrentFolder(page);
    const result = page.locator("#automation-test-result");
    await result.waitFor({timeout: 15_000});
    await result.locator("tr").filter({hasText: "Move 0 photo"}).waitFor();
};

const triggerRows = (page: Page, kind: "Schedule" | "Event"): Locator =>
    page.locator(".automation-trigger-row").filter({
        has: page.locator(".automation-trigger-kind", {hasText: kind}),
    });

export default defineWalkthrough({
    page: "create-customize/automations",

    shots: {
        "automations-list.webp": {
            viewport: {width: 1440, height: 1000},
            goto: `/utilities/automations/${SEEDED_SLUG}`,
            setup: async (page) => {
                await openSeededAutomation(page);
                await removeDocumentationAutomations(page);
                await customAutomationsNav(page).locator("a").filter({hasText: SEEDED_NAME}).waitFor();
                await page.locator(".automation-actions").waitFor();
                await page.mouse.move(0, 0);
            },
        },
        "automation-test.webp": {
            viewport: {width: 1440, height: 1100},
            goto: `/utilities/automations/${SEEDED_SLUG}/edit`,
            clip: ".utilities-content",
            ignoreRegions: ["#automation-test-result .automation-test-meta:first-child"],
            setup: async (page) => {
                await openSeededAutomation(page, "/edit");
                await showDryRun(page);
                await page.mouse.move(0, 0);
            },
        },
        "automation-triggers.webp": {
            viewport: {width: 1440, height: 900},
            goto: `/utilities/automations/${SEEDED_SLUG}/triggers/edit`,
            clip: ".utilities-content",
            setup: async (page) => {
                await openSeededAutomation(page, "/triggers/edit");
                await page.locator(".js-add-schedule").click();
                const addArea = page.locator(".automation-trigger-add.adding-schedule");
                await addArea.waitFor();
                await addArea.locator(".cron-preview").filter({hasText: "Every hour"}).waitFor();
                await page.mouse.move(0, 0);
            },
        },
    },

    flows: async ({page, visit}) => {
        let temporarySlug: string | undefined;
        try {
            await visit(`/utilities/automations/${SEEDED_SLUG}`);
            await waitForAppReady(page);
            await removeDocumentationAutomations(page);

            const systemLinks = systemAutomationsNav(page).locator("a");
            if (await systemLinks.count() !== 7) {
                throw new Error("The Automations page should list seven system automations");
            }
            await customAutomationsNav(page).locator("a").filter({hasText: SEEDED_NAME}).waitFor();
            await page.locator(".automation-actions").getByRole("button", {name: "Enable"}).waitFor();
            await page.getByRole("link", {name: "Edit", exact: true}).waitFor();
            await page.locator("#edit-automation-button").waitFor();
            await page.locator("#delete-automation-button").waitFor();

            // Run… is intentionally cancelled: the automation stays disabled and
            // the fixture keeps an empty run history.
            await page.locator(".js-run-files").click();
            const picker = page.locator("#folder-picker-modal");
            await picker.waitFor();
            await picker.locator("#folder-picker-cancel").click();
            await picker.waitFor({state: "hidden"});
            if (await page.locator("#automation-runs .automation-run-row").count() !== 0) {
                throw new Error("Cancelling Run… unexpectedly created run history");
            }

            // The fixture's reviewed code is safe to execute as a real dry-run:
            // it records an empty move action but writes nothing and creates no Job.
            await openSeededAutomation(page, "/edit");
            await page.locator("#automation-chat-messages .chat-message-user").nth(1).waitFor();
            await page.locator(".automation-code code").filter({hasText: "move_media_items"}).waitFor();
            await showDryRun(page);

            // System automations expose configuration but not custom edit/delete actions.
            await visit("/utilities/automations/auto_assign_faces");
            await waitForAppReady(page);
            if (await page.locator("#edit-automation-button, #delete-automation-button").count() !== 0) {
                throw new Error("A system automation exposed custom-automation actions");
            }
            await page.locator("#configure-automation-button").click();
            const configureModal = page.locator("#configureAutomationModal.active");
            await configureModal.locator("#config-threshold").waitFor();
            await configureModal.locator('.modal-actions [name="cancel"]').click();
            await configureModal.waitFor({state: "hidden"});

            // Create a disposable custom automation and fill in its details through
            // the same controls described by the guide.
            await page.locator("#new-automation-button").click();
            const createModal = page.locator("#newAutomationModal.active");
            await createModal.locator("#new-automation-name").fill(TEMP_NAME);
            await Promise.all([
                page.waitForURL(/\/utilities\/automations\/[a-z0-9-]+$/, {
                    waitUntil: "domcontentloaded",
                }),
                createModal.locator('button[type="submit"]').click(),
            ]);
            temporarySlug = new URL(page.url()).pathname.split("/").pop();
            if (!temporarySlug) throw new Error("The new automation has no slug");
            await waitForAppReady(page);
            await page.locator(".page-header").filter({hasText: TEMP_NAME}).waitFor();

            await page.locator("#edit-automation-button").click();
            const detailsModal = page.locator("#editAutomationModal.active");
            await detailsModal.locator("#edit-automation-name").fill(RENAMED_NAME);
            await detailsModal.locator("#edit-automation-description")
                .fill("Files new arrivals into a dated folder.");
            await Promise.all([
                page.waitForNavigation({waitUntil: "domcontentloaded"}),
                detailsModal.locator('button[type="submit"]').click(),
            ]);
            await waitForAppReady(page);
            await page.locator(".page-header").filter({hasText: RENAMED_NAME}).waitFor();
            await page.locator(".page-header").filter({hasText: "Files new arrivals"}).waitFor();

            // The docs sandbox has no provider key. Simulate only the unavailable
            // external generation responses while exercising the real chat client.
            const prompt = "Tag every photo imported today as New arrival";
            let statusCalls = 0;
            await page.route(`**/utilities/automations/${temporarySlug}/chat`, async (route) => {
                await route.fulfill({
                    status: 202,
                    contentType: "application/json",
                    body: JSON.stringify({slug: temporarySlug}),
                });
            });
            await page.route(`**/utilities/automations/${temporarySlug}/status`, async (route) => {
                statusCalls += 1;
                const running = statusCalls < 3;
                await route.fulfill({
                    status: 200,
                    contentType: "application/json",
                    body: JSON.stringify({
                        slug: temporarySlug,
                        status: running ? "IN_PROGRESS" : "READY",
                        started_at: "2026-01-01T12:00:00Z",
                        working_code: running ? null : "def run(host, context):\n    return None\n",
                        published_code: null,
                        messages: [
                            {type: "user", content: prompt},
                            {type: "assistant", content: "I created the New arrival automation."},
                        ],
                    }),
                });
            });
            await visit(`/utilities/automations/${temporarySlug}/edit`);
            await waitForAppReady(page);
            const reloaded = page.waitForEvent("load", {timeout: 20_000});
            await page.locator("#automation-chat-message").fill(prompt);
            await page.locator('#automation-chat-form button[type="submit"]').click();
            await page.locator("#automation-chat-status").waitFor();
            await page.locator("#automation-chat-messages .chat-message-assistant")
                .filter({hasText: "New arrival automation"}).waitFor();
            await reloaded;
            await page.unrouteAll({behavior: "wait"});

            // Persist one schedule and one event, then remove both. This verifies
            // server validation and the HTMX-rendered trigger lifecycle.
            await visit(`/utilities/automations/${temporarySlug}/triggers/edit`);
            await waitForAppReady(page);
            const addArea = page.locator(".automation-trigger-add");
            await page.locator(".js-add-schedule").click();
            await addArea.locator(".cron-preview").filter({hasText: "Every hour"}).waitFor();
            await addArea.locator(".js-save-schedule").click();
            const schedule = triggerRows(page, "Schedule");
            await schedule.locator(".automation-trigger-desc").filter({hasText: "Every hour"}).waitFor();

            await page.locator(".js-add-event").click();
            await addArea.locator("#new-event-type").selectOption("media_imported");
            await addArea.getByRole("button", {name: "Add event"}).click();
            const event = triggerRows(page, "Event");
            await event.locator(".automation-trigger-event").filter({hasText: "Media imported"}).waitFor();

            await schedule.locator(".btn-danger").click();
            await schedule.waitFor({state: "detached"});
            await event.locator(".btn-danger").click();
            await event.waitFor({state: "detached"});
            await page.locator("#automation-triggers .no-data").waitFor();

            // Delete through the visible confirmation dialog and verify cleanup.
            await visit(`/utilities/automations/${temporarySlug}`);
            await waitForAppReady(page);
            await page.locator("#delete-automation-button").click();
            const confirm = page.locator("#global-confirm-dialog.active");
            await confirm.filter({hasText: RENAMED_NAME}).waitFor();
            await Promise.all([
                page.waitForURL(/\/utilities\/automations\//, {waitUntil: "domcontentloaded"}),
                page.locator("#confirm-dialog-confirm").click(),
            ]);
            await customAutomationsNav(page).locator("a").filter({hasText: RENAMED_NAME})
                .waitFor({state: "detached"});
            temporarySlug = undefined;
        } finally {
            await page.unrouteAll({behavior: "ignoreErrors"}).catch(() => undefined);
            if (temporarySlug) await deleteAutomation(page, temporarySlug).catch(() => undefined);
        }
    },
});
