/**
 * Custom pages walkthrough — docs/guide/create-customize/custom-pages.md
 *
 * The published shots use the seeded Florida Trip page. The flow creates one
 * temporary page, simulates only the external AI generation responses, and
 * deletes the page before returning so the docs fixture stays unchanged.
 */
import type {Locator, Page} from "@playwright/test";
import {defineWalkthrough} from "../../_support";

const SEEDED_PAGE = "Florida Trip";
const TEMP_TITLE = "Documentation Example";
const GENERATED_VERSION_ID = 990001;
const GENERATED_WIDGET_ID = "docsphotocount";

const waitForImage = async (image: Locator): Promise<void> => {
    await image.waitFor();
    await image.evaluate((node: HTMLImageElement) => {
        if (node.complete) return;
        return new Promise<void>((resolve) => {
            node.addEventListener("load", () => resolve(), {once: true});
            node.addEventListener("error", () => resolve(), {once: true});
        });
    });
};

const openSeededPage = async (page: Page, design = false): Promise<void> => {
    const link = page.locator(".nav-page-tab").filter({hasText: SEEDED_PAGE});
    const href = await link.getAttribute("href");
    if (!href?.match(/^\/pages\/\d+$/)) {
        throw new Error(`Could not find the seeded ${SEEDED_PAGE} page`);
    }
    await page.goto(design ? `${href}/design` : href, {waitUntil: "domcontentloaded"});
    await page.locator(design ? ".page-design" : ".page-presentation").waitFor();
};

const waitForSeededWidgets = async (page: Page): Promise<void> => {
    const items = page.locator(".grid-stack .grid-stack-item");
    await items.nth(1).waitFor();
    const hero = page.frameLocator('iframe[title="Hero banner preview"]');
    const gallery = page.frameLocator('iframe[title="Photo gallery preview"]');
    await hero.locator("#hero-title").filter({hasText: "Florida"}).waitFor();
    await waitForImage(hero.locator("#hero-img"));
    const galleryItems = gallery.locator(".gallery-item");
    await galleryItems.nth(12).waitFor();
    await waitForImage(galleryItems.nth(12).locator("img"));
};

const deletePageById = async (page: Page, pageId: number): Promise<void> => {
    const response = await page.request.get("/");
    const html = await response.text();
    const csrfToken = html.match(/name="csrf_token" value="([^"]+)"/)?.[1];
    if (!csrfToken) throw new Error("Could not read a CSRF token for page cleanup");
    const deleted = await page.request.post(`/pages/${pageId}/delete`, {
        form: {csrf_token: csrfToken},
    });
    if (!deleted.ok() && deleted.status() !== 404) {
        throw new Error(`Could not clean up custom page ${pageId}: HTTP ${deleted.status()}`);
    }
};

export default defineWalkthrough({
    page: "create-customize/custom-pages",

    shots: {
        "custom-page-view.webp": {
            viewport: {width: 1400, height: 1500},
            goto: "/",
            clip: ".page-presentation",
            setup: async (page) => {
                await openSeededPage(page);
                await waitForSeededWidgets(page);
                await page.mouse.move(0, 0);
            },
        },
        "custom-page-design.webp": {
            viewport: {width: 1400, height: 1200},
            goto: "/",
            clip: ".page-design",
            setup: async (page) => {
                await openSeededPage(page, true);
                await waitForSeededWidgets(page);
                await page.locator("#conversation-messages .chat-message").nth(3).waitFor();
                await page.mouse.move(0, 0);
            },
        },
    },

    flows: async ({page, visit}) => {
        let temporaryPageId: number | undefined;
        try {
            await visit("/");
            await Promise.all([
                page.waitForURL(/\/pages\/\d+\/design$/, {waitUntil: "domcontentloaded"}),
                page.locator(".nav-new-page").click(),
            ]);
            temporaryPageId = Number(page.url().match(/\/pages\/(\d+)\/design$/)?.[1]);
            if (!temporaryPageId) throw new Error("The new custom page has no id");

            await page.locator("#page-title").fill(TEMP_TITLE);
            await page.locator("#page-subtitle").fill("A temporary page used to verify the editor");
            await page.locator("#page-tab-order").fill("2");
            await page.locator("#page-show-title").check();

            // Manual Add widget creates a blank, client-held draft. Rename it,
            // then Save through the real update route so presentation mode opens.
            await page.locator("#add-widget-button").click();
            const manualWidget = page.locator(".grid-stack .grid-stack-item").first();
            await manualWidget.waitFor();
            await manualWidget.locator(".widget-edit").click();
            await manualWidget.locator(".widget-title-input").fill("Notes");
            await manualWidget.locator(".widget-title-input").press("Enter");
            await Promise.all([
                page.waitForURL(new RegExp(`/pages/${temporaryPageId}$`), {
                    waitUntil: "domcontentloaded",
                }),
                page.locator("#save-page-button").click(),
            ]);
            await page.locator(".page-presentation").waitFor();
            await page.locator(".nav-page-edit").click();
            await page.waitForURL(new RegExp(`/pages/${temporaryPageId}/design$`), {
                waitUntil: "domcontentloaded",
            });

            // The docs sandbox deliberately has no provider key. Exercise the
            // complete client lifecycle with local generation responses while the
            // real preview route renders the returned widget shell.
            const generatedWidget = {
                id: GENERATED_WIDGET_ID,
                title: "Photo count",
                data_query: {},
                state: {},
                html: '<div class="yf-stat"><strong>32</strong><span>Photos</span></div>',
                css: ".yf-stat { display: grid; gap: 8px; padding: 20px; } " +
                    ".yf-stat strong { font-size: 2rem; }",
                js: "",
                grid_x: 0,
                grid_y: 0,
                grid_w: 4,
                grid_h: 3,
            };
            let statusCalls = 0;
            await page.route(`**/pages/${temporaryPageId}/chat`, async (route) => {
                await route.fulfill({
                    status: 202,
                    contentType: "application/json",
                    body: JSON.stringify({version_id: GENERATED_VERSION_ID}),
                });
            });
            await page.route(
                `**/pages/${temporaryPageId}/versions/${GENERATED_VERSION_ID}/status`,
                async (route) => {
                    statusCalls += 1;
                    const running = statusCalls === 1;
                    await route.fulfill({
                        status: 200,
                        contentType: "application/json",
                        body: JSON.stringify({
                            version_id: GENERATED_VERSION_ID,
                            status: running ? "IN_PROGRESS" : "READY",
                            started_at: "2026-01-01T12:00:00Z",
                            completed_at: running ? null : "2026-01-01T12:00:02Z",
                            error: null,
                            messages: [
                                {type: "user", content: "Show a count of my photos"},
                                {type: "assistant", content: "I added a photo-count widget."},
                            ],
                            widgets: running ? [] : [generatedWidget],
                        }),
                    });
                }
            );
            await page.route(
                `**/pages/${temporaryPageId}/versions/${GENERATED_VERSION_ID}/publish`,
                async (route) => route.fulfill({status: 204})
            );

            await page.locator("#conversation-message").fill("Show a count of my photos");
            await page.locator('#conversation-form button[type="submit"]').click();
            await page.locator(".page-design.is-generating").waitFor();
            await page.locator("#conversation-status").waitFor();
            await page.locator(`.grid-stack-item[gs-id="${GENERATED_WIDGET_ID}"]`).waitFor({
                timeout: 15_000,
            });
            await page.waitForFunction(() =>
                !document.querySelector(".page-design")?.classList.contains("is-generating")
            );
            await Promise.all([
                page.waitForURL(new RegExp(`/pages/${temporaryPageId}$`), {
                    waitUntil: "domcontentloaded",
                }),
                page.locator("#save-page-button").click(),
            ]);
            await page.unrouteAll({behavior: "wait"});

            // Delete through the visible confirmation flow and verify its tab is gone.
            await page.locator(".nav-page-edit").click();
            await page.locator("#delete-page-button").click();
            await page.locator("#global-confirm-dialog.active").waitFor();
            await Promise.all([
                page.waitForURL(/\/$/, {waitUntil: "domcontentloaded"}),
                page.locator("#confirm-dialog-confirm").click(),
            ]);
            await page.locator(`nav a[href="/pages/${temporaryPageId}"]`).waitFor({state: "detached"});
            temporaryPageId = undefined;
        } finally {
            await page.unrouteAll({behavior: "ignoreErrors"}).catch(() => undefined);
            if (temporaryPageId) await deletePageById(page, temporaryPageId);
        }
    },
});
