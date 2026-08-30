/**
 * Locations walkthrough — docs/guide/organize-review/locations.md
 *
 * The live OpenStreetMap tile region is ignored only during comparison; the
 * published overview remains an ordinary, unmasked map. Assignment, clearing,
 * and reverse-geocoding requests are fulfilled in Playwright so the shared docs
 * database and external services are never changed.
 */
import type {Page} from "@playwright/test";
import {defineWalkthrough} from "../../_support";

interface LocationFeature {
    get: (key: string) => unknown;
}

interface LocationMapApi {
    map: {
        getSize: () => unknown;
        once: (event: string, callback: () => void) => void;
        renderSync: () => void;
    };
    vectorSource: {getFeatures: () => LocationFeature[]};
    selectedPhotoIds: Set<number>;
    updateSelectionPanel: () => Promise<void>;
}

type LocationsWindow = Window & {
    PHOTO_ORGANIZER: {locations: {map: LocationMapApi}};
};

const PANEL_IMAGES = [
    "2021-07-10_134200_family-sandcastle.png",
    "2021-07-10_141700_family-wading.png",
];

const waitForMap = async (page: Page): Promise<void> => {
    await page.locator("#map").waitFor();
    await page.waitForFunction(() => {
        const api = (window as unknown as LocationsWindow).PHOTO_ORGANIZER?.locations?.map;
        return Boolean(api?.map.getSize() && api.vectorSource.getFeatures().length);
    });
    await page.evaluate(() => new Promise<void>((resolve) => {
        const api = (window as unknown as LocationsWindow).PHOTO_ORGANIZER.locations.map;
        api.map.once("rendercomplete", () => resolve());
        api.map.renderSync();
    }));
};

const selectPanelFixture = async (page: Page): Promise<void> => {
    await page.evaluate(async (filenames) => {
        const api = (window as unknown as LocationsWindow).PHOTO_ORGANIZER.locations.map;
        const matches = api.vectorSource.getFeatures().filter(
            (feature) => filenames.includes(String(feature.get("filename")))
        );
        if (matches.length !== filenames.length) {
            throw new Error(`Expected ${filenames.length} located panel fixtures, found ${matches.length}`);
        }
        api.selectedPhotoIds.clear();
        matches.forEach((feature) => api.selectedPhotoIds.add(Number(feature.get("id"))));
        api.map.renderSync();
        await api.updateSelectionPanel();
    }, PANEL_IMAGES);
    await page.locator("#selection-panel.active").waitFor();
    await page.locator("#selection-panel .btn-recommended").waitFor();
    // Let the panel's width transition finish before calculating the clip.
    await page.waitForTimeout(350);
};

const stubRecommendation = async (page: Page): Promise<void> => {
    await page.route("**/locations/reverse-geocode", async (route) => {
        await route.fulfill({
            status: 200,
            contentType: "application/json",
            body: JSON.stringify({success: true, location_name: "Siesta Key, Florida, USA"}),
        });
    });
};

export default defineWalkthrough({
    page: "organize-review/locations",

    shots: {
        "locations-map.webp": {
            viewport: {width: 1400, height: 1700},
            goto: "/locations",
            clip: ".main-container-layout",
            // OSM is fetched live. Keep it visible in the guide while excluding
            // the complete OpenLayers viewport from per-pixel comparison.
            ignoreRegions: [".ol-viewport"],
            setup: waitForMap,
        },
        "locations-selection-panel.webp": {
            viewport: {width: 1400, height: 1500},
            goto: "/locations",
            clip: "#selection-panel",
            setup: async (page) => {
                await waitForMap(page);
                await stubRecommendation(page);
                await selectPanelFixture(page);
                // The in-app panel scrolls within the map height. For a guide
                // image, size it to its complete content so neither clear action
                // is hidden below that scroll boundary.
                await page.locator("#selection-panel").evaluate((panel) => {
                    panel.style.alignSelf = "flex-start";
                    panel.style.overflow = "visible";
                    const content = panel.querySelector<HTMLElement>("#selection-panel-content");
                    if (content) {
                        content.style.height = "auto";
                        content.style.overflow = "visible";
                    }
                    const summary = panel.querySelector<HTMLElement>(".clusters-summary");
                    if (summary) summary.style.flex = "none";
                });
            },
        },
    },

    flows: async ({page, visit}) => {
        await visit("/locations");
        await waitForMap(page);
        await stubRecommendation(page);
        await selectPanelFixture(page);

        // The multi-photo preview can be collapsed and switched between its
        // stable fixture items without leaving the map.
        await page.locator("#selection-panel .preview-toggle").click();
        await page.locator("#selection-panel .preview-section.collapsed").waitFor();
        await page.locator("#selection-panel .preview-toggle").click();
        await page.locator("#selection-panel .preview-thumb").nth(1).click();

        // Observe both write-capable actions through local successful responses.
        await page.route("**/locations/bulk-update", async (route) => {
            await route.fulfill({
                status: 200,
                contentType: "application/json",
                body: JSON.stringify({success: true, updated_count: PANEL_IMAGES.length}),
            });
        });
        await page.locator("#mass-location-input").fill("Family Beach Trip");
        await page.locator("#mass-assign-btn").click();
        await page.waitForFunction(() =>
            !document.getElementById("selection-panel")?.classList.contains("active")
        );

        await selectPanelFixture(page);
        await page.locator("#selection-panel .btn-clear-names").click();
        await page.waitForFunction(() =>
            !document.getElementById("selection-panel")?.classList.contains("active")
        );
        await page.unroute("**/locations/bulk-update");

        // Every seeded map item has a saved name. The unnamed filter therefore
        // gives the documented no-marker state without changing the URL or view.
        await visit("/locations");
        await waitForMap(page);
        await page.locator("input[name=unnamed]").check();
        await page.locator("#filter-form button[type=submit]").click();
        await page.waitForFunction(() =>
            (window as unknown as LocationsWindow)
                .PHOTO_ORGANIZER.locations.map.vectorSource.getFeatures().length === 0
        );
        await page.locator("#filter-form .clear-filters").click();
        await page.waitForFunction(() =>
            (window as unknown as LocationsWindow)
                .PHOTO_ORGANIZER.locations.map.vectorSource.getFeatures().length > 0
        );

        // Locations owns a page-specific filter layout.
        await page.locator("#configure-filters-btn").click();
        await page.locator("#configureFiltersModal.active").waitFor();
        await page.keyboard.press("Escape");
    },
});
