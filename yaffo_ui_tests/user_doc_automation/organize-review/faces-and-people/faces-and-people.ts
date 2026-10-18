/**
 * Faces and people walkthrough — docs/guide/organize-review/faces-and-people.md
 *
 * The screenshots cover the two overview screens. The flow safely opens the
 * creation, review/removal, help, and automation surfaces without changing the
 * shared documentation fixture.
 */
import {defineWalkthrough} from "../../_support";

const REVIEW_PERSON = "Maya Bennett";

export default defineWalkthrough({
    page: "organize-review/faces-and-people",

    shots: {
        "faces-review.webp": {
            annotations: {
                "unassigned-count": ".main-content[data-unassigned-count] .page-header",
                "assign-or-ignore": ".person-assign-select, #sidebar-assign-selected-btn, #sidebar-ignore-btn",
                "create-person": ".new-person-controls",
                "keyboard-shortcuts": ".keyboard-shortcuts-list:has(.shortcuts-heading)",
                "grouping": ".filter-group:has(#group-by-people), .filter-group:has(#threshold-range)",
                "cluster": "#clusters .suggestion-group:visible >> nth=0",
            },
            viewport: {width: 1400, height: 1600},
            goto: "/faces?group_by=similarity&threshold=50",
            clip: ".main-container-layout",
            setup: async (page) => {
                await page.locator(".suggestion-group:visible .face").first().waitFor();
            },
        },
        "people-list.webp": {
            annotations: {
                "add-person": ".main-content .js-add-person >> nth=0",
                "person": ".people-table .person-name >> nth=0",
                "face-and-photo-counts": ".people-table th:text-is(\"Faces\"), .people-table th:text-is(\"Photos\"), .people-table td[data-label=\"Faces\"], .people-table td[data-label=\"Photos\"]",
                "edit-and-delete": ".people-table tbody tr:first-child .action-link",
            },
            viewport: {width: 1400, height: 900},
            goto: "/people",
            clip: ".main-content",
            setup: async (page) => {
                await page.locator("a.person-name").filter({hasText: REVIEW_PERSON}).waitFor();
            },
        },
    },

    flows: async ({page, visit}) => {
        // The help modal contains the page's grouping concepts and recommended
        // threshold workflow. Observe it and close it without changing state.
        await visit("/faces?group_by=similarity&threshold=50");
        await page.keyboard.press("?");
        await page.locator("#keyboardHelpModal.active").waitFor();
        await page.locator("#keyboardHelpModal [name=cancel]").last().click();

        // The alternate grouping mode and inline create-person controls are part
        // of the same review surface.
        await visit("/faces?group_by=people&threshold=50");
        await page.locator("#create-person-name").waitFor();

        // Open the full person form, then cancel so the fixture stays pristine.
        await visit("/people");
        await page.locator(".js-add-person").first().click();
        await page.locator("#addModal.active").waitFor();
        await page.locator("#addModal [name=cancel]").last().click();

        // Person IDs are reseed-dependent. Follow the stable fixture name to the
        // review page, exercise the removal confirmation, and cancel it.
        const personHref = await page.locator("a.person-name")
            .filter({hasText: REVIEW_PERSON})
            .getAttribute("href");
        if (!personHref) {
            throw new Error(`Seeded person ${REVIEW_PERSON} is missing from People`);
        }
        await visit(personHref);
        await page.locator(".face-card").first().click();
        await page.locator("#remove-selected-faces").click();
        await page.locator("#global-confirm-dialog.active").waitFor();
        await page.locator("#confirm-dialog-cancel").click();

        // The background behavior is configurable on its built-in automation.
        await visit("/utilities/automations/auto_assign_faces");
    },
});
