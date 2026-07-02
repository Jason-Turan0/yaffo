import { test, expect, Page } from '@playwright/test';

// The map is a canvas-rendered OpenLayers view, so DOM assertions can't see
// markers. initMap exposes its API on window.PHOTO_ORGANIZER.locations.map
// ({map, vectorSource, selectedFeatures, ...}); the tests use it for feature
// counts and pixel math, and real mouse input for the interactions.
//
// Serial + ordered on purpose: the mutation tests run last (assign a name,
// then clear it — which restores the seeded unnamed state), and the earlier
// tests compute their expectations from the live data instead of assuming
// which photos are named.
test.describe.configure({ mode: 'serial', timeout: 20_000 });

type MapCounts = { total: number; unnamed: number };

async function openMap(page: Page): Promise<void> {
  await page.goto('/locations');
  await expect(page.locator('#map')).toBeVisible();
  await page.waitForFunction(() => {
    const api = (window as any).PHOTO_ORGANIZER?.locations?.map;
    return !!api && api.map.getSize() != null;
  });
  // Wait for a full render so getPixelFromCoordinate is meaningful
  await page.evaluate(() => new Promise<void>((resolve) => {
    const api = (window as any).PHOTO_ORGANIZER.locations.map;
    api.map.once('rendercomplete', () => resolve());
    api.map.renderSync();
  }));
}

async function featureCounts(page: Page): Promise<MapCounts> {
  return page.evaluate(() => {
    const api = (window as any).PHOTO_ORGANIZER.locations.map;
    const features = api.vectorSource.getFeatures();
    return {
      total: features.length,
      unnamed: features.filter((f: any) => !f.get('name')).length,
    };
  });
}

// Screen position of the first photo feature, relative to the page
async function firstFeaturePoint(page: Page): Promise<{ x: number; y: number }> {
  const pixel = await page.evaluate(() => {
    const api = (window as any).PHOTO_ORGANIZER.locations.map;
    const feature = api.vectorSource.getFeatures()[0];
    return api.map.getPixelFromCoordinate(feature.getGeometry().getCoordinates());
  });
  const box = await page.locator('#map').boundingBox();
  expect(box).not.toBeNull();
  return { x: box!.x + pixel[0], y: box!.y + pixel[1] };
}

// Shift-drag a selection box around every feature on the map
async function shiftDragSelectAll(page: Page): Promise<void> {
  const pixels: number[][] = await page.evaluate(() => {
    const api = (window as any).PHOTO_ORGANIZER.locations.map;
    return api.vectorSource.getFeatures().map((f: any) =>
      api.map.getPixelFromCoordinate(f.getGeometry().getCoordinates()));
  });
  expect(pixels.length).toBeGreaterThan(0);
  const box = (await page.locator('#map').boundingBox())!;
  const xs = pixels.map(p => p[0]);
  const ys = pixels.map(p => p[1]);
  const pad = 40; // cluster circles are ~15px; cover them comfortably
  const x1 = box.x + Math.max(1, Math.min(...xs) - pad);
  const y1 = box.y + Math.max(1, Math.min(...ys) - pad);
  const x2 = box.x + Math.min(box.width - 1, Math.max(...xs) + pad);
  const y2 = box.y + Math.min(box.height - 1, Math.max(...ys) + pad);

  await page.keyboard.down('Shift');
  await page.mouse.move(x1, y1);
  await page.mouse.down();
  await page.mouse.move(x2, y2, { steps: 5 });
  await page.mouse.up();
  await page.keyboard.up('Shift');
}

// The selection panel auto-suggests a name via /locations/reverse-geocode
// (external OpenStreetMap Nominatim) — stub it so tests stay offline-safe
// and deterministic.
async function mockReverseGeocode(page: Page, name: string): Promise<void> {
  await page.route('**/locations/reverse-geocode', route => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({ success: true, location_name: name }),
  }));
}

test.describe('Locations Map', () => {

  test('locations_map_displays_photo_markers - Map renders with markers for photos that have coordinates', async ({ page }) => {
    await openMap(page);

    // OpenLayers rendered its canvas viewport
    await expect(page.locator('#map .ol-viewport canvas').first()).toBeVisible();

    // Every located photo is on the map as a feature
    const counts = await featureCounts(page);
    expect(counts.total).toBeGreaterThan(0);

    // The view was fitted to the data (not the whole-world default zoom 2)
    const zoom = await page.evaluate(() =>
      (window as any).PHOTO_ORGANIZER.locations.map.map.getView().getZoom());
    expect(zoom).toBeGreaterThan(2);
  });

  test('locations_popup_shows_photo_details - Clicking a marker opens a popup with the photo, closing hides it', async ({ page }) => {
    await openMap(page);

    const popup = page.locator('#popup');
    await expect(popup).toBeHidden();

    const point = await firstFeaturePoint(page);
    await page.mouse.click(point.x, point.y);

    await expect(popup).toBeVisible();
    const popupImage = popup.locator('img.popup-photo');
    await expect(popupImage).toBeVisible();

    // The thumbnail source is servable and not the fallback placeholder
    const src = await popupImage.getAttribute('src');
    const fallbackSrc = await popupImage.getAttribute('data-fallback');
    expect(src).not.toBeNull();
    expect(src).not.toContain(fallbackSrc!);
    const response = await page.request.get(src!, { failOnStatusCode: false });
    expect(response.ok(), `Popup thumbnail "${src}" failed with status ${response.status()}`).toBe(true);

    // Filename heading and a link to the media detail view (the popup's first
    // <a> is the closer, so scope to the content area)
    await expect(popup.locator('h3')).not.toBeEmpty();
    expect(await popup.locator('#popup-content a').first().getAttribute('href')).toMatch(/\/media\/view\/\d+/);

    // The closer hides the popup again
    await page.locator('#popup-closer').click();
    await expect(popup).toBeHidden();
  });

  test('locations_filter_unnamed_photos - The unnamed-only filter narrows the map and unchecking restores it', async ({ page }) => {
    await openMap(page);
    const before = await featureCounts(page);

    // Turn the filter on: only photos without a location name remain
    await page.locator('#filter-unnamed').check();
    await expect.poll(() => featureCounts(page)).toEqual({
      total: before.unnamed,
      unnamed: before.unnamed,
    });

    // Turn it off: the full located set comes back
    await page.locator('#filter-unnamed').uncheck();
    await expect.poll(() => featureCounts(page)).toEqual(before);
  });

  test('locations_reverse_geocode_suggests_name - Selecting a cluster surfaces a reverse-geocoded name suggestion', async ({ page }) => {
    const SUGGESTED = 'Mocked Beach Town';
    let geocodedBody: { lat?: number; lon?: number } = {};
    await page.route('**/locations/reverse-geocode', route => {
      geocodedBody = route.request().postDataJSON();
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ success: true, location_name: SUGGESTED }),
      });
    });

    await openMap(page);
    await shiftDragSelectAll(page);

    const panel = page.locator('#selection-panel');
    await expect(panel).toHaveClass(/active/);

    // The suggestion arrives as a recommended quick-assign button
    const recommended = panel.locator('.btn-recommended');
    await expect(recommended).toBeVisible();
    await expect(recommended).toContainText(SUGGESTED);

    // The lookup used the selected cluster's coordinates
    const coords = await page.evaluate(() => {
      const api = (window as any).PHOTO_ORGANIZER.locations.map;
      const f = api.vectorSource.getFeatures()[0];
      const geom = f.getGeometry().getCoordinates();
      // OL stores web-mercator; ol.proj.toLonLat converts back
      return (window as any).ol.proj.toLonLat(geom);
    });
    expect(Math.abs(geocodedBody.lat! - coords[1])).toBeLessThan(0.5);
    expect(Math.abs(geocodedBody.lon! - coords[0])).toBeLessThan(0.5);
  });

  test('locations_select_clusters_and_assign_name - Shift-drag selection assigns a location name to the photos', async ({ page }) => {
    const LOCATION_NAME = 'Test Beach';
    await mockReverseGeocode(page, 'Somewhere Else');
    await openMap(page);

    // Remember which photos we are about to rename
    const photoIds: number[] = await page.evaluate(() =>
      (window as any).PHOTO_ORGANIZER.locations.map.vectorSource.getFeatures()
        .map((f: any) => f.get('id')));

    await shiftDragSelectAll(page);
    const panel = page.locator('#selection-panel');
    await expect(panel).toHaveClass(/active/);
    await expect(panel.locator('.mass-assignment-info')).not.toBeEmpty();

    // Type the custom name and assign it to everything selected
    await panel.locator('#mass-location-input').fill(LOCATION_NAME);
    const [response] = await Promise.all([
      page.waitForResponse(resp => resp.url().includes('/locations/bulk-update')),
      panel.locator('#mass-assign-btn').click(),
    ]);
    expect(response.ok()).toBeTruthy();
    await expect(page.locator('.notification.visible')).toBeVisible();

    // The selection clears and the map features carry the new name
    await expect(panel).not.toHaveClass(/active/);
    const named = await page.evaluate((expected) => {
      const api = (window as any).PHOTO_ORGANIZER.locations.map;
      return api.vectorSource.getFeatures().every((f: any) => f.get('name') === expected);
    }, LOCATION_NAME);
    expect(named).toBe(true);

    // And the server persisted it — the photo's detail page shows the name
    const detailHtml = await (await page.request.get(`/media/view/${photoIds[0]}`)).text();
    expect(detailHtml).toContain(LOCATION_NAME);

    // Cleanup happens in the next (serial) test: clearing the names restores
    // the seeded unnamed state, so suite re-runs start from a clean slate.
  });

  test('locations_clear_location_names - Clearing removes the names from the selected photos', async ({ page }) => {
    await mockReverseGeocode(page, 'Somewhere Else');
    await openMap(page);

    const photoIds: number[] = await page.evaluate(() =>
      (window as any).PHOTO_ORGANIZER.locations.map.vectorSource.getFeatures()
        .map((f: any) => f.get('id')));

    await shiftDragSelectAll(page);
    const panel = page.locator('#selection-panel');
    await expect(panel).toHaveClass(/active/);

    // Clear the names from everything selected
    const [response] = await Promise.all([
      page.waitForResponse(resp => resp.url().includes('/locations/bulk-update')),
      panel.locator('.btn-clear-names').click(),
    ]);
    expect(response.ok()).toBeTruthy();
    expect(response.request().postDataJSON()).toMatchObject({ clear: true });
    await expect(page.locator('.notification.visible')).toBeVisible();

    // The selection clears and every feature is unnamed again
    await expect(panel).not.toHaveClass(/active/);
    const counts = await featureCounts(page);
    expect(counts.unnamed).toBe(counts.total);

    // Server state matches: the detail page no longer shows the assigned name
    const detailHtml = await (await page.request.get(`/media/view/${photoIds[0]}`)).text();
    expect(detailHtml).not.toContain('Test Beach');
  });
});
