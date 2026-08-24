/**
 * Face-assignment walkthrough — docs/guide/organize-review/assigning-faces.md
 *
 * Every write-capable action is fulfilled in the browser. This lets the guide
 * show and exercise assignment progress without changing the shared fixture.
 */
import type {Page} from "@playwright/test";
import {defineWalkthrough} from "../../_support";

const FACES_URL = "/faces?group_by=similarity&threshold=50";
const PERSON_NAME = "Maya Bennett";

const waitForCluster = async (page: Page): Promise<void> => {
    await page.locator(".suggestion-group:visible .face").first().waitFor();
};

const choosePerson = async (page: Page, name = PERSON_NAME): Promise<void> => {
    const control = page.locator(".person-assign-select .searchable-select-wrapper");
    await control.locator(".searchable-select-display").click();
    await control.locator(".searchable-select-option").filter({hasText: name}).click();
    await control.locator(".searchable-select-text").filter({hasText: name}).waitFor();
};

const stubFaceAssignment = async (page: Page): Promise<void> => {
    await page.route("**/api/faces/assign", async (route) => {
        const body = route.request().postDataJSON() as {
            faces?: unknown[];
            faceStatus?: string;
        };
        const count = body.faces?.length ?? 0;
        const action = body.faceStatus === "IGNORED" ? "ignored" : "assigned";
        await route.fulfill({
            contentType: "application/json",
            body: JSON.stringify({
                success: true,
                message: `Successfully ${action} ${count} faces`,
            }),
        });
    });
};

export default defineWalkthrough({
    page: "organize-review/assigning-faces",

    shots: {
        "faces-assign-01-overview.webp": {
            viewport: {width: 1400, height: 1600},
            goto: FACES_URL,
            clip: ".main-container-layout",
            setup: waitForCluster,
        },
        "faces-assign-02-controls.webp": {
            viewport: {width: 1400, height: 1600},
            goto: FACES_URL,
            clip: ".sidebar-container",
            setup: waitForCluster,
        },
        "faces-assign-03-pick-person.webp": {
            viewport: {width: 1400, height: 1000},
            goto: FACES_URL,
            clip: ".sidebar-actions",
            setup: async (page) => {
                await waitForCluster(page);
                await page.locator(".person-assign-select .searchable-select-display").click();
                await page.locator(".person-assign-select .searchable-select-dropdown").waitFor();
            },
        },
        "faces-assign-04-refine.webp": {
            viewport: {width: 1400, height: 900},
            goto: FACES_URL,
            clip: ".suggestion-group:visible",
            setup: async (page) => {
                await waitForCluster(page);
                await choosePerson(page);
                await page.locator(".suggestion-group:visible .face").first().click();
                await page.mouse.move(1390, 890);
            },
        },
        "faces-assign-05-quick-assign.webp": {
            viewport: {width: 1400, height: 900},
            goto: "/faces?group_by=people&threshold=50",
            clip: ".suggestion-group:visible",
            setup: async (page) => {
                await waitForCluster(page);
                await page.locator(".suggestion-group:visible .assign-group-btn").first().waitFor();
            },
        },
    },

    flows: async ({page, visit}) => {
        await visit(FACES_URL);
        await waitForCluster(page);

        // Observe selection controls and the hover preview without changing data.
        const activeGroup = page.locator(".suggestion-group:visible");
        await activeGroup.locator(".cluster-select-all").click();
        await activeGroup.locator(".cluster-select-all").click();
        await activeGroup.locator(".face").first().hover();
        await page.locator(".face-tooltip.visible").waitFor();

        // Help and shortcut configuration are modal-only until Save is pressed.
        await page.keyboard.press("?");
        await page.locator("#keyboardHelpModal.active").waitFor();
        await page.locator("#keyboardHelpModal [name=cancel]").last().click();
        await page.locator("#configure-shortcuts-btn").click();
        await page.locator("#shortcutPeopleModal.active").waitFor();
        await page.locator("#shortcutPeopleModal [name=cancel]").last().click();

        // Exercise sidebar assignment and automatic cluster advancement through
        // a browser-local response, never the fixture's write endpoint.
        await stubFaceAssignment(page);
        await choosePerson(page);
        const initialCluster = await activeGroup.getAttribute("data-cluster-index");
        await page.locator("#sidebar-assign-selected-btn").click();
        await page.waitForFunction(() =>
            document.querySelector(".notification")?.classList.contains("visible")
        );
        await page.waitForFunction(
            (index) => document.querySelector<HTMLElement>(".suggestion-group:not([hidden])")
                ?.dataset.clusterIndex !== index,
            initialCluster
        );
        await page.unroute("**/api/faces/assign");

        // Ignoring uses the same endpoint with a different status. Stub it on a
        // fresh page, then verify the request rather than persisting the result.
        await visit(FACES_URL);
        await waitForCluster(page);
        await stubFaceAssignment(page);
        const ignored = page.waitForRequest((request) =>
            request.url().endsWith("/api/faces/assign")
            && request.postDataJSON()?.faceStatus === "IGNORED"
        );
        await page.locator("#sidebar-ignore-btn").click();
        await ignored;
        await page.unroute("**/api/faces/assign");

        // People mode owns the quick assignment button; Skip advances without a
        // write and leaves the faces available for a later pass.
        await visit("/faces?group_by=people&threshold=50");
        await waitForCluster(page);
        await page.locator(".suggestion-group:visible .assign-group-btn").first().waitFor();
        await page.locator(".suggestion-group:visible .skip-cluster-btn").click();
    },
});
