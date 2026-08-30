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
    await page.locator("#scan-results").getByText("Everything is in sync").waitFor();
};

export default defineWalkthrough({
    page: "reference-maintenance/troubleshooting",

    shots: {
        "index-photos-status.webp": {
            viewport: {width: 1400, height: 1000},
            goto: "/utilities/index-photos",
            clip: ".utility-page",
            setup: waitForIndexScan,
        },
        "ai-generation-status.webp": {
            viewport: {width: 1400, height: 1000},
            goto: "/settings",
            clip: "#llm-section",
            setup: async (page) => {
                // The enhanced native select is intentionally hidden behind its
                // searchable-select wrapper once initialization completes.
                await page.locator("#llm-model[data-searchable-initialized]")
                    .waitFor({state: "attached"});
                await page.locator("#llm-model + .searchable-select-wrapper").waitFor();
                await page.locator("#llm-api-key").waitFor();
                await page.locator("#llm-section").scrollIntoViewIfNeeded();
            },
        },
    },

    flows: async ({page, visit}) => {
        // Missing-photo diagnosis begins with the configured folders and a real,
        // read-only scan of the documentation fixture.
        await visit("/settings");
        await page.locator("#media-dirs-list .media-dir-item").first().waitFor();
        await visit("/utilities/index-photos");
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
        await page.locator("#llm-api-key").waitFor();
    },
});
