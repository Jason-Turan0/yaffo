/**
 * Media-detail walkthrough — docs/guide/library-basics/photo-details.md
 *
 * The screenshot uses a stable, information-rich birthday photo. Flows cover the
 * interactive controls without persisting changes or opening host applications.
 */
import {defineWalkthrough} from "../../_support";

const DETAIL_IMAGE = "2017-09-12_162200_blowing-candles.png";
const PLAYABLE_VIDEO = "2021-07-11_113104_boy-and-the-waves.mp4";

export default defineWalkthrough({
    page: "library-basics/photo-details",

    shots: {
        "media-detail.webp": {
            viewport: {width: 1400, height: 1250},
            goto: ({mediaIdByFilename}) =>
                mediaIdByFilename(DETAIL_IMAGE).then((id) => `/media/view/${id}`),
            clip: ".photo-viewer",
            // The docs fixture is /private/tmp on macOS and /tmp on Linux. Keep
            // the useful folder visible while excluding only its host spelling.
            ignoreRegions: [
                ".detail-section:first-child .detail-item:nth-of-type(2) .detail-value",
            ],
        },
    },

    flows: async ({page, visit, mediaIdByFilename}) => {
        const photoId = await mediaIdByFilename(DETAIL_IMAGE);
        await visit(`/media/view/${photoId}`);

        // Hover draws the face box; clicking opens the reassignment overlay.
        const firstFace = page.locator(".face-thumbnail").first();
        await firstFace.hover();
        await firstFace.waitFor({state: "visible"});
        await firstFace.click();
        const faceOverlay = page.locator(".face-reassign-controls");
        await faceOverlay.waitFor({state: "visible"});
        await faceOverlay.locator("[data-action='cancel']").click();

        // The framework stubs these OS-launching endpoints during every capture.
        await page.getByRole("button", {name: "Open File", exact: true}).click();
        await page.getByRole("button", {name: "Open Folder", exact: true}).click();

        // Exercise favorite state without changing the seeded database.
        await page.route(`**/api/media/${photoId}/favorite`, async (route) => {
            await route.fulfill({
                contentType: "application/json",
                body: JSON.stringify({favorite: true}),
            });
        });
        const favorite = page.locator(".favorite-toggle");
        await favorite.click();
        await favorite.waitFor({state: "visible"});
        await page.unroute(`**/api/media/${photoId}/favorite`);

        // Open and exercise the tag editor, then cancel without saving.
        await page.getByRole("button", {name: "Edit Tags"}).click();
        const tagsModal = page.locator("#tagsModal");
        await tagsModal.waitFor({state: "visible"});
        await tagsModal.locator("#modal-new-tag-name").fill("trip");
        await tagsModal.locator("#modal-new-tag-value").fill("birthday");
        await tagsModal.getByRole("button", {name: "Add Tag"}).click();
        await tagsModal.locator(".tag-editor-item").waitFor({state: "visible"});
        await tagsModal.getByRole("button", {name: "Cancel"}).click();

        // Reindexing drops this item's face assignments, so observe and cancel
        // the confirmation rather than starting destructive background work.
        await page.locator("#reindex-btn").click();
        await page.locator("#global-confirm-dialog.active").waitFor();
        await page.locator("#confirm-dialog-cancel").click();

        // The seeded MP4 covers inline playback plus video-only file metadata.
        // Non-playable containers use another branch of the same observed template;
        // the docs fixture intentionally contains no synthetic container copy.
        const videoId = await mediaIdByFilename(PLAYABLE_VIDEO);
        await visit(`/media/view/${videoId}`);
        await page.locator("#mainVideo").waitFor({state: "visible"});

        // Escape returns to the originating Yaffo page.
        await visit(`/?view=grid&path=${encodeURIComponent(DETAIL_IMAGE)}`);
        // Gallery cards normally open details in a new tab. Reuse this capture tab
        // while preserving the same gallery -> detail browser-history sequence.
        await visit(`/media/view/${photoId}`);
        await page.keyboard.press("Escape");
        await page.waitForURL((url) => url.pathname === "/");
    },
});
