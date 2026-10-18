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
            annotations: {
                "filter-sidebar": "#home-filters",
                "photo-count": ".photo-gallery .page-header .subtitle",
                "grid-and-timeline": ".view-toggle",
                "media-card": ".photo-grid .photo-card >> nth=0",
            },
            viewport: {width: 1400, height: 1100},
            // Pin the gallery view and the beach trip so the shot includes photo
            // cards alongside the real video's play badge and duration.
            goto: "/?view=grid&year=2021",
            clip: ".main-container-layout",
            // End on a whole row; a raw height cut slices the bottom tiles in half.
            rows: {grid: ".photo-grid", item: ".photo-card", count: 2},
        },

        // "Use the Filter Sidebar"
        "gallery-filter-sidebar.webp": {
            annotations: {
                "sidebar-settings": "#configure-filters-btn",
                "year-and-month": ".filter-group:has(#year-select), .filter-group:has(#month-select)",
                "location-filters": ".filter-group:has(.location-proximity-filter) > :not(.location-proximity-filter)",
                "proximity-search": ".location-proximity-filter",
                "apply-or-clear": "#filter-form .filter-btn, #filter-form .clear-filters",
            },
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
