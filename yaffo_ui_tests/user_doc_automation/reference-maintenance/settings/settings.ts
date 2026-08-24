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
