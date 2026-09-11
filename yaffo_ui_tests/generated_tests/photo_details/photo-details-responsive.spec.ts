import { rename } from 'node:fs/promises';
import { join } from 'node:path';
import { test, expect, Page } from '@playwright/test';
import { findFirstPhotoIdWithDetectedFaces, findMediaIdByFilename, PRIMARY_DETAIL_IMAGE } from '../_support/media-test-data';
import {
  CONTRACT_WIDTHS,
  VIEWPORTS,
  expectFitsViewport,
  expectNoPageOverflow,
  expectRouteFits,
  withTouchContext,
} from '../_support/responsive';

// Responsive coverage for the media detail viewer (P1). The shared shell
// contract itself is asserted on Home (specs/photo_gallery.yaml); everything
// here is this page's own narrow-screen behaviour.

/** True once the photo AND its face-overlay canvas have real dimensions. */
async function waitForFaceCanvas(page: Page): Promise<void> {
  await page.waitForFunction(() => {
    const img = document.getElementById('mainPhoto') as HTMLImageElement | null;
    const canvas = document.getElementById('faceCanvas') as HTMLCanvasElement | null;
    return !!img && img.complete && img.naturalWidth > 0
      && !!canvas && canvas.width > 0 && canvas.height > 0;
  });
}

/** Whether anything is currently painted on the face-overlay canvas. */
async function faceOverlayIsPainted(page: Page): Promise<boolean> {
  return page.locator('#faceCanvas').evaluate((canvas: HTMLCanvasElement) => {
    const ctx = canvas.getContext('2d');
    if (!ctx || !canvas.width || !canvas.height) return false;
    const { data } = ctx.getImageData(0, 0, canvas.width, canvas.height);
    return data.some((value, index) => index % 4 === 3 && value > 0);
  });
}

/**
 * Elements that own a vertical scroll region. The stacked layout must leave the
 * document as the only scroller: a pane scrolling inside the page is how the
 * metadata became unreachable on a phone.
 */
async function nestedScrollRegions(page: Page): Promise<string[]> {
  return page.evaluate(() => Array
    .from(document.querySelectorAll<HTMLElement>('.photo-viewer, .photo-container, .photo-wrapper, .photo-sidebar, .sidebar-content'))
    .filter((element) => {
      const overflowY = getComputedStyle(element).overflowY;
      return (overflowY === 'auto' || overflowY === 'scroll')
        && element.scrollHeight > element.clientHeight + 1;
    })
    .map((element) => element.className));
}

test.describe('Photo Details - Responsive', () => {
  test('photo_details_fits_every_contract_viewport - the detail route never scrolls the page sideways', async ({ page, request }) => {
    const photoId = await findMediaIdByFilename(request, PRIMARY_DETAIL_IMAGE);

    for (const width of CONTRACT_WIDTHS) {
      await page.setViewportSize({ width, height: 800 });
      await expectRouteFits(page, `/media/view/${photoId}`);
      await expect(page.locator('.photo-main')).toBeVisible();
    }
  });

  test('photo_details_stacks_media_above_metadata - the photo leads and the details scroll with the document', async ({ page, request }) => {
    const photoId = await findFirstPhotoIdWithDetectedFaces(request);
    await page.setViewportSize(VIEWPORTS.narrow);
    await page.goto(`/media/view/${photoId}`);

    await expect(page.locator('.photo-main')).toBeVisible();
    await expectNoPageOverflow(page);

    // The media pane renders above the details pane even though the sidebar
    // comes first in source order (it is ordered, not re-rendered).
    const media = (await page.locator('.photo-container').boundingBox())!;
    const details = (await page.locator('.photo-sidebar').boundingBox())!;
    expect(media.y).toBeLessThan(details.y);

    // The photo is on the first screen rather than pushed off it, and it does
    // not eat the whole viewport — the details announce themselves below.
    expect(media.y).toBeLessThan(page.viewportSize()!.height);
    expect(await page.locator('.photo-main').evaluate((element) => element.getBoundingClientRect().height))
      .toBeLessThan(page.viewportSize()!.height);

    // REGRESSION - the sidebar used to keep its desktop framing at narrow
    // widths (a 40vh sticky card whose .sidebar-content was its own scroller),
    // so the metadata could only be reached by scrolling inside a box that
    // looked like the page. The document is now the only scroller.
    expect(await nestedScrollRegions(page)).toEqual([]);

    // Every metadata surface is present and reachable by page scroll.
    for (const heading of [/File Information/, /Location/, /People? \(/, /Faces? \(/, /Tags? \(/]) {
      await expect(page.locator('.detail-section h3').filter({ hasText: heading }).first()).toBeAttached();
    }
    const favorite = page.locator('.photo-image-container .favorite-toggle');
    await expect(favorite).toBeVisible();
    await expectFitsViewport(page, '.photo-image-container .favorite-toggle');
  });

  test('photo_details_survives_rotation - rotating to a short landscape keeps the media contained', async ({ page, request }) => {
    const photoId = await findMediaIdByFilename(request, PRIMARY_DETAIL_IMAGE);
    await page.setViewportSize(VIEWPORTS.narrow);
    await page.goto(`/media/view/${photoId}`);
    await expect(page.locator('.photo-main')).toBeVisible();

    // Rotation, not a reload: the same document has to cope.
    await page.setViewportSize(VIEWPORTS.narrowLandscape);
    await expect(page.locator('.photo-main')).toBeVisible();
    await expectNoPageOverflow(page);
    expect(await nestedScrollRegions(page)).toEqual([]);

    // The media still fits the short viewport rather than being cropped by it.
    const mediaHeight = await page.locator('.photo-main').evaluate((element) => element.getBoundingClientRect().height);
    expect(mediaHeight).toBeGreaterThan(0);
    expect(mediaHeight).toBeLessThanOrEqual(VIEWPORTS.narrowLandscape.height);

    // REGRESSION - the viewer sized itself with fixed vh calculations, which on
    // a phone measure the viewport as if the browser chrome were not there and
    // push the actions under it. Every viewport-bound height is declared in dvh
    // (with a vh line before it as the fallback).
    const dynamicUnitRules = await page.evaluate(() => Array.from(document.styleSheets)
      .filter((sheet) => (sheet.href || '').includes('media/view.css'))
      .flatMap((sheet) => Array.from(sheet.cssRules))
      .filter((rule) => rule.cssText.includes('dvh'))
      .length);
    expect(dynamicUnitRules).toBeGreaterThan(0);
  });

  test('photo_details_face_highlight_works_with_a_coarse_pointer - tapping a face draws its box', async ({ browser, request }) => {
    const photoId = await findFirstPhotoIdWithDetectedFaces(request);

    await withTouchContext(browser, VIEWPORTS.narrow, async (page) => {
      await page.goto(`/media/view/${photoId}`);
      await waitForFaceCanvas(page);
      expect(await faceOverlayIsPainted(page)).toBe(false);

      // REGRESSION - the face box was drawn only from the thumbnail's
      // mouseenter, an event a touch screen never fires, so the whole
      // face-to-photo mapping was unreachable with a coarse pointer.
      const firstFace = page.locator('.faces-grid .face-thumbnail').first();
      await expect(firstFace).toBeVisible();
      await firstFace.tap();

      await expect(firstFace).toHaveClass(/highlighted/);
      expect(await faceOverlayIsPainted(page)).toBe(true);
    });
  });

  test('photo_details_face_highlight_survives_rotation - resizing redraws the box instead of losing it', async ({ browser, request }) => {
    const photoId = await findFirstPhotoIdWithDetectedFaces(request);

    // A touch context, because a mouse-driven highlight is ended by the
    // mouseleave the reflow itself fires — rotation is a touch-screen event.
    await withTouchContext(browser, VIEWPORTS.narrow, async (page) => {
      await page.goto(`/media/view/${photoId}`);
      await waitForFaceCanvas(page);

      const firstFace = page.locator('.faces-grid .face-thumbnail').first();
      await firstFace.tap();
      await expect(firstFace).toHaveClass(/highlighted/);
      expect(await faceOverlayIsPainted(page)).toBe(true);

      // REGRESSION - resizing the canvas to the photo's new rendered size wipes
      // it, so a rotation used to leave the thumbnail marked highlighted with no
      // box on the photo. The overlay is redrawn at the new scale instead.
      await page.setViewportSize(VIEWPORTS.narrowLandscape);
      await expect(firstFace).toHaveClass(/highlighted/);
      await expect.poll(() => faceOverlayIsPainted(page)).toBe(true);
    });
  });

  test('photo_details_tag_editor_fits_a_narrow_viewport - the tag dialog and its add row work at 320px', async ({ page, request }) => {
    const photoId = await findMediaIdByFilename(request, PRIMARY_DETAIL_IMAGE);
    await page.setViewportSize(VIEWPORTS.minimum);
    await page.goto(`/media/view/${photoId}`);

    await page.getByRole('button', { name: 'Edit Tags' }).click();
    const modal = page.locator('#tagsModal');
    await expect(modal).toHaveClass(/active/);
    await expectFitsViewport(page, '#tagsModal .modal-content');

    // REGRESSION - two text inputs and the Add button shared one flex row, so
    // at 320px each input collapsed to a few pixels. The row stacks instead.
    await expect(page.locator('.tag-add-row')).toHaveCSS('flex-direction', 'column');
    const nameInput = page.locator('#modal-new-tag-name');
    await expect(nameInput).toBeVisible();
    expect((await nameInput.boundingBox())!.width).toBeGreaterThan(150);

    // Scroll ownership: the dialog body contains its own overflow.
    await expectNoPageOverflow(page);
    const bodyOverflow = await page.locator('#tagsModal .modal-body').evaluate(
      (element) => getComputedStyle(element).overflowY);
    expect(['auto', 'scroll']).toContain(bodyOverflow);
  });

  test('photo_details_long_path_does_not_widen_the_page - an unbreakable folder path wraps instead', async ({ page, request }) => {
    const photoId = await findMediaIdByFilename(request, PRIMARY_DETAIL_IMAGE);
    await page.setViewportSize(VIEWPORTS.minimum);
    await page.goto(`/media/view/${photoId}`);

    // A long-content fixture: real libraries carry deep unbroken paths and
    // camera-generated names with no spaces to wrap at.
    await page.locator('.detail-section .detail-value').first().evaluate((element) => {
      element.textContent = `/Volumes/${'photo-archive-directory'.repeat(12)}/IMG_20260830_120000000000.png`;
    });
    await expectNoPageOverflow(page);
  });

  test('photo_details_video_fits_a_narrow_viewport - a video detail page contains its player', async ({ page, request }) => {
    const listing = await request.get('/?media-type=video&page-size=50');
    expect(listing.ok()).toBeTruthy();
    const match = (await listing.text()).match(/\/media\/view\/(\d+)/);
    expect(match, 'Expected at least one seeded video').not.toBeNull();

    await page.setViewportSize(VIEWPORTS.narrow);
    await page.goto(`/media/view/${match![1]}`);

    const player = page.locator('video.photo-main, img.photo-main, .media-missing');
    await expect(player.first()).toBeVisible();
    await expectNoPageOverflow(page);
    await expectFitsViewport(page, '.photo-image-container');
    expect(await nestedScrollRegions(page)).toEqual([]);
  });

  test('photo_details_missing_video_state_fits_a_narrow_viewport - a missing source remains a usable detail page', async ({ page, request }) => {
    const listing = await request.get('/?media-type=video&page-size=50');
    expect(listing.ok()).toBeTruthy();
    const videoIds = [...new Set([...((await listing.text()).matchAll(/\/media\/view\/(\d+)/g))]
      .map((match) => match[1]))];
    expect(videoIds.length, 'Expected at least one seeded video').toBeGreaterThan(0);

    await page.setViewportSize(VIEWPORTS.narrow);
    await page.goto(`/media/view/${videoIds.at(-1)}`);

    const fileInfo = page.locator('.detail-section').filter({ hasText: 'File Information' });
    const fileName = (await fileInfo.locator('.detail-item').filter({ hasText: 'Name:' })
      .locator('.detail-value').innerText()).trim();
    const folder = (await fileInfo.locator('.detail-item').filter({ hasText: 'Folder:' })
      .locator('.detail-value').innerText()).trim();
    const sourcePath = join(folder, fileName);
    const heldPath = `${sourcePath}.responsive-missing`;

    await rename(sourcePath, heldPath);
    try {
      await page.reload();
      const missing = page.locator('.media-missing');
      await expect(missing).toBeVisible();
      await expect(missing.locator('.media-missing-name')).toHaveText(fileName);
      await expectFitsViewport(page, '.media-missing');
      await expectNoPageOverflow(page);
      expect(await nestedScrollRegions(page)).toEqual([]);
    } finally {
      await rename(heldPath, sourcePath);
    }
  });

  test('photo_details_metadata_actions_are_touch_sized - people, location and tag actions stay operable', async ({ browser, request }) => {
    const photoId = await findFirstPhotoIdWithDetectedFaces(request);

    await withTouchContext(browser, VIEWPORTS.narrow, async (page) => {
      await page.goto(`/media/view/${photoId}`);
      await expectNoPageOverflow(page);

      // REGRESSION - a person chip is the only route from this photo to that
      // person, and the shared coarse-pointer sizing does not reach a bare <a>,
      // so it stayed a 28px pill on a touch screen.
      const person = page.locator('.people-list .person-link').first();
      if (await person.count() > 0) {
        expect((await person.boundingBox())!.height).toBeGreaterThanOrEqual(44);
      }

      // The file, tag and (where present) map actions are reachable and sized.
      for (const name of ['Open File', 'Open Folder', 'Reindex', 'Edit Tags']) {
        const button = page.getByRole('button', { name });
        await expect(button).toBeVisible();
        expect((await button.boundingBox())!.height).toBeGreaterThanOrEqual(44);
      }
      const map = page.locator('.location-details .action-button');
      if (await map.count() > 0) {
        expect((await map.first().boundingBox())!.height).toBeGreaterThanOrEqual(44);
      }
    });
  });
});
