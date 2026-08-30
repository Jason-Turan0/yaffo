/**
 * Label-classification walkthrough — docs/guide/organize-review/labels.md
 *
 * Vocabulary writes and whole-library reclassification are intercepted in the
 * browser. Gallery filtering and detail review remain real, read-only requests.
 */
import type {Page} from "@playwright/test";
import {defineWalkthrough} from "../../_support";

const LABELS_ROUTE = "**/settings/labels";
const LABEL_DETAIL_IMAGE = "2017-09-12_162200_blowing-candles.png";

const waitForLabels = async (page: Page): Promise<void> => {
    const section = page.locator("#labels-section");
    await section.locator(".label-chip").first().waitFor();
    await section.scrollIntoViewIfNeeded();
};

const stubVocabularyWrites = async (page: Page): Promise<void> => {
    await page.route(LABELS_ROUTE, async (route) => {
        await route.fulfill({status: 204});
    });
};

export default defineWalkthrough({
    page: "organize-review/labels",

    shots: {
        "settings-labels.webp": {
            viewport: {width: 1400, height: 2200},
            goto: "/settings",
            clip: "#labels-section",
            setup: waitForLabels,
        },
        "classify-labels-automation.webp": {
            viewport: {width: 1400, height: 1000},
            goto: "/utilities/automations/classify_labels",
            clip: "#configureAutomationModal .modal-content",
            setup: async (page) => {
                await page.locator("#configure-automation-button").click();
                await page.locator("#configureAutomationModal.active").waitFor();
            },
        },
        "media-labels.webp": {
            viewport: {width: 1400, height: 1100},
            goto: ({mediaIdByFilename}) =>
                mediaIdByFilename(LABEL_DETAIL_IMAGE).then((id) => `/media/view/${id}`),
            clip: ".detail-section:has(.labels-chips)",
            setup: async (page) => {
                const section = page.locator(".detail-section:has(.labels-chips)");
                await section.locator(".label-chip").first().waitFor();
                await section.scrollIntoViewIfNeeded();
            },
        },
    },

    flows: async ({page, visit, mediaIdByFilename}) => {
        await visit("/settings");
        await waitForLabels(page);

        // The filter searches both names and prompts. "people swimming" exists
        // only in the seeded swimming prompt, so it covers the latter branch.
        await page.locator("#label-filter").fill("people swimming");
        await page.locator("#labels-section .label-chip:visible .label-chip-name")
            .filter({hasText: "swimming"})
            .waitFor();
        await page.locator("#label-filter").fill("");

        // Drive create, toggle, and delete without allowing an HTMX request to
        // reach the fixture database.
        await stubVocabularyWrites(page);
        const section = page.locator("#labels-section");
        await section.locator("input[name=name]").fill("kayak");
        await section.locator("input[name=prompt]").fill("people kayaking on a lake");
        let vocabularyRequest = page.waitForRequest((request) =>
            request.url().endsWith("/settings/labels")
            && request.postData()?.includes("action=create") === true
        );
        await section.locator(".add-label-form button[type=submit]").click();
        await vocabularyRequest;

        const dog = section.locator(".label-chip").filter({
            has: page.locator(".label-chip-name", {hasText: /^dog$/}),
        });
        vocabularyRequest = page.waitForRequest((request) =>
            request.url().endsWith("/settings/labels")
            && request.postData()?.includes("action=toggle") === true
        );
        await dog.locator(".label-chip-toggle").click();
        await vocabularyRequest;

        vocabularyRequest = page.waitForRequest((request) =>
            request.url().endsWith("/settings/labels")
            && request.postData()?.includes("action=delete") === true
        );
        await dog.locator(".label-chip-remove").click();
        await vocabularyRequest;
        await page.unroute(LABELS_ROUTE);

        // Reclassification is another write-capable background action. Observe
        // the HTMX request through a local 204 response instead of enqueueing it.
        await page.route("**/settings/labels/reclassify", async (route) => {
            await route.fulfill({status: 204});
        });
        const reclassifyRequest = page.waitForRequest((request) =>
            request.url().endsWith("/settings/labels/reclassify")
        );
        await section.locator(".labels-reclassify button").click();
        await reclassifyRequest;
        await page.unroute("**/settings/labels/reclassify");

        // The automation page exposes the Media indexed trigger and configuration.
        await visit("/utilities/automations/classify_labels");
        await page.locator("#configure-automation-button").click();
        await page.locator("#configureAutomationModal.active").waitFor();
        await page.locator("#configureAutomationModal [name=cancel]").last().click();

        // Select two stable labels in the real gallery UI, choose an ALL match,
        // and submit the read-only filter form.
        await visit("/?view=grid");
        const labelPicker = page.locator(".multi-select-wrapper")
            .filter({has: page.locator("input[name=labels]")});
        await labelPicker.locator(".multi-select-header").click();
        for (const label of ["birthday party", "cake"]) {
            await labelPicker.locator(".multi-select-option")
                .filter({hasText: label})
                .locator("input[name=labels]")
                .check();
        }
        await page.locator("#labels-match-type .match-option")
            .filter({hasText: "All of these"})
            .click();
        await Promise.all([
            page.waitForURL(
                (url) => url.searchParams.getAll("labels").length === 2,
                {waitUntil: "domcontentloaded"}
            ),
            page.locator("#filter-form button[type=submit]").click(),
        ]);
        await page.locator(".photo-card").first().waitFor();

        // The information-rich birthday fixture owns two known label chips and
        // their confidence tooltips.
        const mediaId = await mediaIdByFilename(LABEL_DETAIL_IMAGE);
        await visit(`/media/view/${mediaId}`);
        await page.locator(".detail-section:has(.labels-chips) .label-chip").first().waitFor();
    },
});
