/**
 * Glossary walkthrough — docs/guide/start-here/concepts.md
 *
 * This page owns no screenshots. Its walkthrough visits the app surfaces that
 * give the glossary terms their concrete meaning so dependency scoping remains
 * useful even though capture produces no images.
 */
import {defineWalkthrough} from "../../_support";

const DETAIL_IMAGE = "2017-09-12_162200_blowing-candles.png";

export default defineWalkthrough({
    page: "start-here/concepts",
    shots: {},

    flows: async ({page, visit, mediaIdByFilename}) => {
        // Library, media items, index, thumbnails, favorites, and filters.
        await visit("/?view=grid");
        await visit("/settings");
        await visit("/utilities/index-photos");

        // Background jobs are rendered as fragments within utility pages. Visit
        // the fragment directly so its route and template are observed too.
        await visit("/jobs/section?job_name=index_photos&has_results=false");

        // Tags, labels, favorite state, locations, people, and detected faces.
        const mediaId = await mediaIdByFilename(DETAIL_IMAGE);
        await visit(`/media/view/${mediaId}`);
        await visit("/people");
        await visit("/faces?group_by=people&threshold=100");
        await visit("/locations");

        // Albums, duplicate review, automations, and themes.
        await visit("/albums");
        await visit("/utilities/remove-duplicates");
        await visit("/utilities/automations");
        await visit("/themes");

        // Custom page IDs belong to fixture data, so resolve the seeded page from
        // its navigation entry rather than baking its current database ID in here.
        await visit("/?view=grid");
        const customPageHref = await page.locator(".nav-page-tab")
            .filter({hasText: "Florida Trip"})
            .first()
            .getAttribute("href");
        if (!customPageHref) {
            throw new Error("Seeded Florida Trip custom page is missing from navigation");
        }
        await visit(customPageHref);
    },
});
