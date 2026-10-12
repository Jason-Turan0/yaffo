/**
 * Troubleshooting walkthrough — docs/guide/reference-maintenance/troubleshooting.md
 *
 * The screenshots show the two central diagnostic surfaces. The remaining flow is
 * read-only: it visits each feature-specific place named by the guide so changes to
 * those routes and templates invalidate this page without changing the docs fixture.
 */
import type {Page} from "@playwright/test";
import {defineWalkthrough} from "../../_support";

const waitForIndexScan = async (page: Page): Promise<void> => {
    await page.waitForFunction(
        () => document.querySelector("#stat-orphaned")?.textContent?.trim() !== "—",
        undefined,
        {timeout: 30_000}
    );
    await page.locator("#status-in-sync").getByText("Everything is in sync").waitFor();
};

/**
 * The provider starts empty; choosing one swaps in its model list and key settings.
 * With no key saved, the lookup uses the starter list and calls no provider.
 */
const chooseProvider = async (page: Page, label = "Anthropic"): Promise<void> => {
    const provider = page.locator("#llm-provider + .searchable-select-wrapper");
    await provider.locator(".searchable-select-display").click();
    await provider.locator(".searchable-select-option").filter({hasText: label}).click();
    await page.locator("#llm-model[data-searchable-initialized]").waitFor({state: "attached"});
    await page.locator("#llm-model + .searchable-select-wrapper").waitFor();
    await page.locator("#llm-api-key").waitFor();
};

export default defineWalkthrough({
    page: "reference-maintenance/troubleshooting",

    shots: {
        "library-health-status.webp": {
            viewport: {width: 1400, height: 1000},
            goto: "/library/health",
            clip: ".utility-page",
            setup: waitForIndexScan,
        },
        "ai-generation-status.webp": {
            viewport: {width: 1400, height: 1000},
            goto: "/settings",
            clip: "#llm-section",
            setup: async (page) => {
                await chooseProvider(page);
                await page.locator("#llm-section").scrollIntoViewIfNeeded();
                await page.mouse.move(0, 0);
            },
        },
    },

    flows: async ({page, visit}) => {
        // Missing-photo diagnosis begins with the configured folders and a real,
        // read-only scan of the documentation fixture.
        await visit("/settings");
        await page.locator("#media-dirs-list .media-dir-item").first().waitFor();
        await visit("/library/health");
        await waitForIndexScan(page);

        // These are the correction and review surfaces linked from the ML section.
        await visit("/faces");
        await page.locator("#threshold-range").waitFor();
        await visit("/settings");
        await page.locator("#labels-section").waitFor();
        await visit("/utilities/remove-duplicates");
        await page.locator("#remove-duplicates-form").waitFor();

        // The map itself uses live tiles; loading its application surface is enough
        // to observe location-route dependencies for this diagnostic page.
        await visit("/locations");
        await page.locator("#map").waitFor();

        // AI troubleshooting is anchored in provider/model/key status. Do not submit
        // the form: the docs sandbox deliberately has no real provider credentials.
        await visit("/settings");
        await page.locator("#llm-section").waitFor();
        await chooseProvider(page);
    },
});
