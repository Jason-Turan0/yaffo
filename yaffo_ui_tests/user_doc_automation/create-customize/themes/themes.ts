/**
 * Themes walkthrough — docs/guide/create-customize/themes.md
 *
 * Published shots use the seeded Test Ocean theme. The flow creates one
 * temporary theme, exercises its user-facing lifecycle, and always restores the
 * original default and fixture state.
 */
import type {Page} from "@playwright/test";
import {defineWalkthrough} from "../../_support";

const SEEDED_THEME = "Test Ocean";
const TEMP_PREFIX = "Documentation Theme";
const TEMP_LABEL = `${TEMP_PREFIX} Example`;
const RENAMED_LABEL = `${TEMP_PREFIX} Coastal`;

const customThemesNav = (page: Page) =>
    page.locator('.themes-sidebar h3:has-text("Custom") + ul.panel-nav');

const systemThemesNav = (page: Page) =>
    page.locator('.themes-sidebar h3:has-text("System") + ul.panel-nav');

const csrfToken = async (page: Page): Promise<string> => {
    const token = await page.evaluate(() => (
        window as Window & {APP_CONFIG?: {csrfToken?: string}}
    ).APP_CONFIG?.csrfToken);
    if (!token) throw new Error("The themes page did not expose a CSRF token");
    return token;
};

const deleteTheme = async (page: Page, slug: string): Promise<void> => {
    const response = await page.request.post(`/themes/${slug}/delete`, {
        headers: {"X-CSRF-Token": await csrfToken(page)},
    });
    if (!response.ok() && response.status() !== 404) {
        throw new Error(`Could not clean up theme ${slug}: HTTP ${response.status()}`);
    }
};

const setDefault = async (page: Page, slug: string): Promise<void> => {
    const response = await page.request.post(`/themes/${slug}/default`, {
        headers: {"X-CSRF-Token": await csrfToken(page)},
    });
    if (!response.ok()) {
        throw new Error(`Could not restore default theme ${slug}: HTTP ${response.status()}`);
    }
};

const removeDocumentationThemes = async (page: Page): Promise<void> => {
    const links = customThemesNav(page).locator("a");
    const count = await links.count();
    const slugs: string[] = [];
    for (let index = 0; index < count; index += 1) {
        const link = links.nth(index);
        const label = (await link.textContent())?.trim() ?? "";
        const href = await link.getAttribute("href");
        if (label.startsWith(TEMP_PREFIX) && href?.startsWith("/themes/")) {
            slugs.push(href.slice("/themes/".length));
        }
    }
    for (const slug of slugs) await deleteTheme(page, slug);
    if (slugs.length > 0) await page.reload({waitUntil: "domcontentloaded"});
};

const prepareSeededTheme = async (page: Page, slug: string): Promise<void> => {
    await removeDocumentationThemes(page);
    await page.goto(`/themes/${slug}`, {waitUntil: "domcontentloaded"});
    await page.locator(".themes-container").waitFor();
    await page.mouse.move(0, 0);
};

export default defineWalkthrough({
    page: "create-customize/themes",

    shots: {
        "themes-list.webp": {
            viewport: {width: 1400, height: 900},
            goto: "/themes/classic",
            setup: async (page) => {
                await prepareSeededTheme(page, "classic");
                await systemThemesNav(page).locator("a").nth(5).waitFor();
                await customThemesNav(page).locator("a").filter({hasText: SEEDED_THEME}).waitFor();
            },
        },
        "theme-editor.webp": {
            viewport: {width: 1400, height: 900},
            goto: "/themes/test-ocean",
            setup: async (page) => {
                await prepareSeededTheme(page, "test-ocean");
                await page.locator("#theme-chat-messages .chat-message").nth(1).waitFor();
                await page.locator("#rename-theme-button").waitFor();
            },
        },
    },

    flows: async ({page, visit}) => {
        let temporarySlug: string | undefined;
        let originalDefault: string | undefined;
        try {
            await visit("/themes");
            await removeDocumentationThemes(page);
            originalDefault = new URL(page.url()).pathname.split("/").pop();

            const systemLinks = systemThemesNav(page).locator("a");
            if (await systemLinks.count() !== 6) {
                throw new Error("The Themes page should list six system themes");
            }
            await customThemesNav(page).locator("a").filter({hasText: SEEDED_THEME}).waitFor();
            await page.locator(".themes-sidebar .theme-nav-default").waitFor();

            await page.locator("#new-theme-button").click();
            const createModal = page.locator("#newThemeModal.active");
            await createModal.waitFor();
            await createModal.locator("#new-theme-label").fill(TEMP_LABEL);
            await Promise.all([
                page.waitForURL(/\/themes\/[a-z0-9-]+$/, {waitUntil: "domcontentloaded"}),
                createModal.locator('button[type="submit"]').click(),
            ]);
            temporarySlug = new URL(page.url()).pathname.split("/").pop();
            if (!temporarySlug) throw new Error("The new theme has no slug");
            await page.locator(".page-header").filter({hasText: TEMP_LABEL}).waitFor();

            // The documentation sandbox has no provider key. Simulate only the
            // external generation responses while exercising the real chat UI.
            let statusCalls = 0;
            await page.route(`**/themes/${temporarySlug}/chat`, async (route) => {
                await route.fulfill({
                    status: 202,
                    contentType: "application/json",
                    body: JSON.stringify({slug: temporarySlug}),
                });
            });
            await page.route(`**/themes/${temporarySlug}/status`, async (route) => {
                statusCalls += 1;
                const running = statusCalls < 3;
                await route.fulfill({
                    status: 200,
                    contentType: "application/json",
                    body: JSON.stringify({
                        slug: temporarySlug,
                        status: running ? "IN_PROGRESS" : "READY",
                        started_at: "2026-01-01T12:00:00Z",
                        messages: [
                            {type: "user", content: "A calm coastal theme"},
                            {type: "assistant", content: "I designed a calm coastal theme."},
                        ],
                    }),
                });
            });
            const reloaded = page.waitForEvent("load", {timeout: 20_000});
            await page.locator("#theme-chat-message").fill("A calm coastal theme");
            await page.locator('#theme-chat-form button[type="submit"]').click();
            await page.locator("#theme-chat-status").waitFor();
            await page.locator("#theme-chat-messages .chat-message-assistant")
                .filter({hasText: "calm coastal theme"}).waitFor();
            await reloaded;
            await page.unrouteAll({behavior: "wait"});

            await page.locator("#rename-theme-button").click();
            const renameModal = page.locator("#renameThemeModal.active");
            await renameModal.waitFor();
            await renameModal.locator("#rename-theme-label").fill(RENAMED_LABEL);
            await Promise.all([
                page.waitForURL(/\/themes\/[a-z0-9-]+$/, {waitUntil: "domcontentloaded"}),
                renameModal.locator('button[type="submit"]').click(),
            ]);
            temporarySlug = new URL(page.url()).pathname.split("/").pop();
            await page.locator(".page-header").filter({hasText: RENAMED_LABEL}).waitFor();

            await page.getByRole("button", {name: "Make default"}).click();
            await customThemesNav(page).locator("li").filter({hasText: RENAMED_LABEL})
                .locator(".theme-nav-default").waitFor();
            await visit("/people");
            await page.locator(`html[data-theme="${temporarySlug}"]`).waitFor();
            if (originalDefault) await setDefault(page, originalDefault);

            await visit(`/themes/${temporarySlug}`);
            await page.locator("#delete-theme-button").click();
            await page.locator("#global-confirm-dialog.active").waitFor();
            await Promise.all([
                page.waitForURL(/\/themes\/[a-z0-9-]+$/, {waitUntil: "domcontentloaded"}),
                page.locator("#confirm-dialog-confirm").click(),
            ]);
            await customThemesNav(page).locator("a").filter({hasText: RENAMED_LABEL})
                .waitFor({state: "detached"});
            temporarySlug = undefined;

            await visit("/themes/classic");
            if (await page.locator("#rename-theme-button, #delete-theme-button").count() !== 0) {
                throw new Error("A system theme exposed custom-theme actions");
            }
        } finally {
            await page.unrouteAll({behavior: "ignoreErrors"}).catch(() => undefined);
            if (originalDefault) await setDefault(page, originalDefault).catch(() => undefined);
            if (temporarySlug) await deleteTheme(page, temporarySlug).catch(() => undefined);
        }
    },
});
