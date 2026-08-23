import {defineWalkthrough} from "../../_support";

export default defineWalkthrough({
    page: "reference-maintenance/settings",

    shots: {
        "settings-overview.webp": {
            viewport: {width: 1400, height: 2200},
            goto: "/settings",
            clip: ".main-content",
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
