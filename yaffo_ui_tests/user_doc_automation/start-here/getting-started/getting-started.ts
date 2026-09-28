/**
 * Tour walkthrough — docs/guide/start-here/getting-started.md
 *
 * This converts the five framing definitions from the original documentation
 * screenshot proof of concept into the reproducible walkthrough pipeline. The page
 * is intentionally a tour: its shots revisit views documented in more depth elsewhere.
 */
import {defineWalkthrough} from "../../_support";

const DETAIL_IMAGE = "2017-09-12_162200_blowing-candles.png";

export default defineWalkthrough({
    page: "start-here/getting-started",

    shots: {
        // "Choose Your Photo Folders" — only the introductory Settings sections
        // belong in this tour; the complete page has its own Settings reference.
        "settings-overview.webp": {
            viewport: {width: 1400, height: 1200},
            goto: "/settings",
            clip: ".main-content",
            // Paths stay visible in the capture but must not make diffs noisy: macOS
            // canonicalizes /tmp to /private/tmp while Linux leaves it alone, and the
            // thumbnail stats are counted from the generated fixture thumbnails, so
            // they move whenever that cache is rebuilt.
            ignoreRegions: [".media-dir-path", "#current-thumbnail-dir", "#thumbnail-stats"],
            setup: async (page) => {
                await page.evaluate(() => {
                    const sections = Array.from(document.querySelectorAll(".settings-section"));
                    sections.slice(3).forEach((section) => section.remove());
                });
            },
        },

        // "Index Your First Photos"
        "library-health.webp": {
            viewport: {width: 1400, height: 1000},
            goto: "/library/health",
            clip: ".utility-page",
            setup: async (page) => {
                // The scan streams after the initial page. Do not capture its em-dash
                // placeholders or a partial count.
                await page.waitForFunction(
                    () => document.querySelector("#stat-total-filesystem")
                        ?.textContent?.trim() !== "—",
                    undefined,
                    {timeout: 30_000}
                );
            },
        },

        // "Browse Your Library"
        "gallery-home.webp": {
            viewport: {width: 1400, height: 1100},
            // Grid is persisted server-side, so pin it instead of inheriting the
            // state left by another walkthrough.
            goto: "/?view=grid",
            clip: ".main-container-layout",
            rows: {grid: ".photo-grid", item: ".photo-card", count: 2},
        },

        "gallery-filter-sidebar.webp": {
            // Tall enough for the entire sidebar; otherwise its box is viewport-capped.
            viewport: {width: 1400, height: 2000},
            goto: "/?view=grid",
            clip: ".sidebar",
        },

        // "Open a Photo" — IDs change whenever the fixture is rebuilt, so resolve
        // the same documented birthday photo from its stable filename.
        "media-detail.webp": {
            viewport: {width: 1400, height: 1150},
            goto: ({mediaIdByFilename}) =>
                mediaIdByFilename(DETAIL_IMAGE).then((id) => `/media/view/${id}`),
            clip: ".photo-viewer",
            // The changed line is the FOLDER value's first line: the docs fixture
            // is /private/tmp on macOS and /tmp on Linux, so that line follows the
            // host, not the app. Keep the folder visible in the capture and exclude
            // only its host spelling from comparison — the same region the
            // library-basics/photo-details page masks for this same view.
            ignoreRegions: [
                ".detail-section:first-child .detail-item:nth-of-type(2) .detail-value",
            ],
        },
    },
});
