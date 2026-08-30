/**
 * Reference walkthrough — docs/guide/library-basics/browsing-filtering.md
 *
 * A walkthrough has two outputs, and only the first is optional:
 *
 *   1. the screenshots this page shows, captured to staging;
 *   2. this page's runtime dependency set, from every route, template, and static
 *      asset touched while driving it.
 *
 * `shots` covers the views the page illustrates. `flows` covers the rest of what
 * the page describes — sections that have prose but no picture. Driving those is
 * the only way their source files enter the dependency set, which is why a page
 * that shows no images at all still gets a walkthrough.
 */
import {defineWalkthrough} from "../../_support";

export default defineWalkthrough({
    page: "library-basics/browsing-filtering",

    shots: {
        // "Browse the Gallery"
        "gallery-home.webp": {
            viewport: {width: 1400, height: 1100},
            // Two pieces of state are pinned here, for different reasons.
            //
            // `view` is persisted server-side and the timeline scrubber rewrites it,
            // so an unpinned "/" inherits whatever the last visitor left behind.
            //
            // `year` is a workaround, not a preference. The shared fixture carries
            // two 1mb-example-video-file test patterns that remove_duplicates needs,
            // and being the newest videos they land in row two and dominate the shot.
            // Scoping to the 2021 trip gives 13 real photos and one real clip, so the
            // card affordances the page describes (play badge, duration) are still
            // visible. Drop this once the docs run has its own fixture composition —
            // see "Fixture work still required" in the plan.
            goto: "/?view=grid&year=2021",
            clip: ".main-container-layout",
            // End on a whole row; a raw height cut slices the bottom tiles in half.
            rows: {grid: ".photo-grid", item: ".photo-card", count: 2},
        },

        // "Use the Filter Sidebar"
        "gallery-filter-sidebar.webp": {
            // Tall enough for the whole sidebar to lay out. The element's own box is
            // capped by the viewport, so a short viewport truncates the shot mid-filter
            // instead of failing.
            viewport: {width: 1400, height: 2000},
            goto: "/?view=grid",
            clip: ".sidebar",
        },
    },

    flows: async ({page, visit}) => {
        // "Useful Searches" — a filtered gallery on a different axis to the shot's,
        // so both filter branches are exercised. Driven through the query string
        // rather than the widgets: the sidebar's searchable-select controls are custom
        // and do not respond to Playwright's selectOption, and the server renders the
        // same templates either way.
        await visit("/?view=grid&year=2017");

        // "When Results Look Wrong" — the empty state is its own template branch.
        await visit("/?view=grid&year=1900");

        // "Match Any or All" — the any/all radios are display:none until a multi-select
        // is opened, so the control has to be revealed rather than requested by URL.
        await visit("/?view=grid");
        await page.locator(".multi-select-header").first().click();
        await page.locator("#person-match-type input[value='all']").first()
            .waitFor({state: "attached"});

        // "Configure the Sidebar" — the config modal is a separate fragment.
        await page.locator("#configure-filters-btn").click();
        await page.locator("#filter-config-list").waitFor({state: "visible"});
        await page.keyboard.press("Escape");

        // "Clear Filters" — returns to the unfiltered gallery.
        await visit("/?view=grid&year=2021");
        await page.locator(".clear-filters").click();
        await page.waitForLoadState("domcontentloaded");
    },
});
