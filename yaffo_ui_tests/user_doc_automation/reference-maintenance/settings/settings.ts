import {defineWalkthrough} from "../../_support";

export default defineWalkthrough({
    page: "reference-maintenance/settings",

    shots: {
        "settings-overview.webp": {
            viewport: {width: 1400, height: 2200},
            goto: "/settings",
            clip: ".main-content",
            // These values legitimately vary by host: macOS canonicalizes /tmp to
            // /private/tmp, and generated thumbnail bytes vary slightly by platform.
            // Keep them visible in the guide while excluding them from pixel comparison.
            ignoreRegions: [".media-dir-path", "#current-thumbnail-dir", "#thumbnail-size"],
            setup: async (page) => {
                // The "Pages" strip under the navbar is now a server-persisted
                // preference (base.html renders `hidden` from `show_pages_bar` and
                // nav.js saves the toggle through PUT /pages/bar-visibility), so it
                // is shared state: the custom-pages walkthrough expands the strip and
                // that choice now sticks for every later visit. The clip below ends at
                // the viewport bottom, so an expanded strip would shorten this capture
                // by the strip's height depending on which walkthrough ran first. Pin it
                // collapsed — the state a fresh install renders — instead of inheriting
                // whatever the shared docs database last saved.
                await page.evaluate(() => {
                    const bar = document.getElementById("navbar-pages-bar");
                    if (bar) bar.hidden = true;
                });

                // Wait for thumbnail stats stream to settle so count and size are rendered
                await page.waitForFunction(() => {
                    const el = document.getElementById("thumbnail-size");
                    return el && el.textContent !== "Counting…";
                });
            },
        },
    },

    flows: async ({page, visit}) => {
        await visit("/settings");

        // Exercise label filter input
        const labelFilter = page.locator("#label-filter");
        if (await labelFilter.isVisible()) {
            await labelFilter.fill("dog");
            await page.waitForTimeout(100);
            await labelFilter.clear();
        }
    },
});
