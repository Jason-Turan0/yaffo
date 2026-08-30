/**
 * Indexing lifecycle walkthrough — docs/guide/library-basics/indexing-library.md
 *
 * The committed shot shows the normal, settled library. The flow also drives the
 * new-file/orphan branches with a synthetic scan response, without changing the
 * shared documentation fixture or starting background work.
 */
import type {Page} from "@playwright/test";
import {defineWalkthrough} from "../../_support";

const waitForScan = async (page: Page): Promise<void> => {
    await page.waitForFunction(
        () => document.querySelector("#stat-orphaned")?.textContent?.trim() !== "—",
        undefined,
        {timeout: 30_000}
    );
};

export default defineWalkthrough({
    page: "library-basics/indexing-library",

    shots: {
        "utilities-index-photos.webp": {
            viewport: {width: 1400, height: 1000},
            goto: "/utilities/index-photos",
            clip: ".utility-page",
            setup: waitForScan,
        },
    },

    flows: async ({page, visit}) => {
        // Media-directory configuration is the start of the indexing lifecycle.
        await visit("/settings");

        // Exercise the two scan result tables and Sync button without writing to
        // the fixture. The real scan route was already observed by the shot above.
        await page.route("**/utilities/index-photos/scan", async (route) => {
            await route.fulfill({
                contentType: "application/x-ndjson",
                body: `${JSON.stringify({
                    type: "done",
                    total_filesystem: 29,
                    total_imported: 29,
                    total_indexed: 28,
                    unindexed: [{
                        filename: "new-photo.jpg",
                        full_path: "/docs-fixture/new-photo.jpg",
                    }],
                    orphaned: [{
                        id: 30,
                        reason: "missing",
                        full_path: "/docs-fixture/missing-photo.jpg",
                    }],
                })}\n`,
            });
        });
        await visit("/utilities/index-photos");
        await waitForScan(page);
        await page.locator("#scan-results").getByText("Unindexed Photos").waitFor();
        await page.locator("#scan-results").getByText("Orphaned Database Entries").waitFor();
        await page.locator("#sync-button").waitFor({state: "visible"});
        await page.unroute("**/utilities/index-photos/scan");

        // Reindex is destructive to face assignments. Observe its confirmation
        // contract and cancel instead of starting the job.
        await visit("/utilities/index-photos");
        await waitForScan(page);
        await page.locator("#reindex-button").click();
        await page.locator("#global-confirm-dialog.active").waitFor();
        await page.locator("#confirm-dialog-cancel").click();

        // The page describes both job cards and the built-in hourly File sync.
        await visit("/jobs/section?job_name=index_photos&has_results=false");
        await visit("/utilities/automations/file_sync");
    },
});
