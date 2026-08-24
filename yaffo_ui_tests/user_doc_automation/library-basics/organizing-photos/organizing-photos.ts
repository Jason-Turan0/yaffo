/**
 * Organization-hub walkthrough — docs/guide/library-basics/organizing-photos.md
 *
 * This page explains relationships rather than screens, so it owns no images.
 * Read-only visits keep every organization surface in its dependency scope.
 */
import {defineWalkthrough} from "../../_support";

const DETAIL_IMAGE = "2017-09-12_162200_blowing-candles.png";

export default defineWalkthrough({
    page: "library-basics/organizing-photos",
    shots: {},

    flows: async ({page, visit, mediaIdByFilename}) => {
        // Indexing and temporary gallery views.
        await visit("/utilities/index-photos");
        await visit("/?view=grid&year=2021");
        await visit("/?view=grid&favorite=1");

        // The detail view owns favorites and manual tags and also shows the
        // people, labels, and resolved location attached to one media item.
        const mediaId = await mediaIdByFilename(DETAIL_IMAGE);
        await visit(`/media/view/${mediaId}`);

        // Review surfaces for people, faces, label vocabulary, and locations.
        await visit("/people");
        await visit("/faces?group_by=people&threshold=100");
        await visit("/settings");
        await visit("/locations");

        // Cleanup and exact curated collections.
        await visit("/utilities/remove-duplicates");
        await visit("/albums");

        // Custom page IDs come from fixture data, so follow the seeded page's
        // navigation link instead of assuming its current database ID.
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
