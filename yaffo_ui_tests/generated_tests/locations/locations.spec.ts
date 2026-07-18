import { test, expect, Page } from '@playwright/test';

// OpenLayers renders markers on canvas, so marker visibility and cluster state
// are asserted through the map API exposed at window.PHOTO_ORGANIZER.locations.map.
test.describe.configure({ mode: 'serial', timeout: 30_000 });

type MapCounts = { total: number; unnamed: number };
type Point = { x: number; y: number };
type ViewState = { center: number[]; zoom: number };
type ClusterSummary = {
  total: number;
  selected: number;
  ids: number[];
  pixel: number[];
  iconSrc: string | null;
};

const TEST_LOCATION_NAME = 'Test Beach';
const WHITE_HOUSE_LOCATION_NAME = 'The White House';
const NAMED_WHITE_HOUSE_IMAGE = 'whitehouse_2014_01282014.jpg';
const SELECTED_WHITE_HOUSE_IMAGE = 'whitehouse_2014_03012014.jpg';
const CHICAGO_IMAGE = 'obama-family-photo-celebration-1514413986.jpg';
const CHICAGO_GEOCODE_NAME = 'Mocked Chicago Grant Park';

async function openMap(page: Page): Promise<void> {
  await page.goto('/locations');
  await expect(page.locator('#map')).toBeVisible();
  await page.waitForFunction(() => {
    const api = (window as any).PHOTO_ORGANIZER?.locations?.map;
    return !!api && api.map.getSize() != null && api.vectorSource.getFeatures().length > 0;
  });
  await waitForMapRender(page);
}

async function waitForMapRender(page: Page): Promise<void> {
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

async function selectedIds(page: Page): Promise<number[]> {
  return page.evaluate(() =>
    Array.from((window as any).PHOTO_ORGANIZER.locations.map.selectedPhotoIds as Set<number>).sort());
}

async function allFeatureIds(page: Page): Promise<number[]> {
  return page.evaluate(() =>
    (window as any).PHOTO_ORGANIZER.locations.map.vectorSource.getFeatures().map((f: any) => f.get('id')).sort());
}

async function featureIdByImageName(page: Page, imageName: string): Promise<number> {
  const id = await page.evaluate((filename) => {
    const api = (window as any).PHOTO_ORGANIZER.locations.map;
    const feature = api.vectorSource.getFeatures().find((candidate: any) => candidate.get('filename') === filename);
    return feature?.get('id') ?? null;
  }, imageName);
  expect(id, `Expected a located feature for ${imageName}`).not.toBeNull();
  return id!;
}

async function viewState(page: Page): Promise<ViewState> {
  return page.evaluate(() => {
    const view = (window as any).PHOTO_ORGANIZER.locations.map.map.getView();
    return { center: view.getCenter(), zoom: view.getZoom() };
  });
}

function expectSameView(actual: ViewState, expected: ViewState): void {
  expect(actual.zoom).toBeCloseTo(expected.zoom, 6);
  expect(actual.center[0]).toBeCloseTo(expected.center[0], 4);
  expect(actual.center[1]).toBeCloseTo(expected.center[1], 4);
}

async function clusterSummaries(page: Page): Promise<ClusterSummary[]> {
  return page.evaluate(() => {
    const api = (window as any).PHOTO_ORGANIZER.locations.map;
    const clusterLayer = api.map.getLayers().getArray().find((layer: any) =>
      layer.getSource?.()?.getFeatures?.()?.some((feature: any) => Array.isArray(feature.get('features'))));
    return clusterLayer.getSource().getFeatures().map((cluster: any) => {
      const features = cluster.get('features');
      const selected = features.filter((feature: any) => api.selectedPhotoIds.has(feature.get('id'))).length;
      const style = clusterLayer.getStyle()(cluster);
      return {
        total: features.length,
        selected,
        ids: features.map((feature: any) => feature.get('id')).sort(),
        pixel: api.map.getPixelFromCoordinate(cluster.getGeometry().getCoordinates()),
        iconSrc: style?.getImage?.()?.getSrc?.() ?? null,
      };
    }).sort((a: ClusterSummary, b: ClusterSummary) => b.total - a.total || a.ids[0] - b.ids[0]);
  });
}

async function screenPointForCluster(page: Page, cluster: ClusterSummary): Promise<Point> {
  const box = await page.locator('#map').boundingBox();
  expect(box).not.toBeNull();
  return { x: box!.x + cluster.pixel[0], y: box!.y + cluster.pixel[1] };
}

async function clickCluster(page: Page, cluster: ClusterSummary, modifiers: { shift?: boolean } = {}): Promise<void> {
  const point = await screenPointForCluster(page, cluster);
  if (modifiers.shift) await page.keyboard.down('Shift');
  await page.mouse.click(point.x, point.y);
  if (modifiers.shift) await page.keyboard.up('Shift');
}

async function clickFirstCluster(page: Page, modifiers: { shift?: boolean } = {}): Promise<ClusterSummary> {
  const clusters = await clusterSummaries(page);
  expect(clusters.length).toBeGreaterThan(0);
  await clickCluster(page, clusters[0], modifiers);
  return clusters[0];
}

async function zoomToFeaturesByImageName(page: Page, imageNames: string[]): Promise<void> {
  await page.evaluate((filenames) => {
    const api = (window as any).PHOTO_ORGANIZER.locations.map;
    const features = api.vectorSource.getFeatures().filter((feature: any) => filenames.includes(feature.get('filename')));
    if (features.length !== filenames.length) {
      throw new Error(`Located feature count mismatch. Expected ${filenames.length}, found ${features.length}.`);
    }

    const view = api.map.getView();
    if (features.length === 1) {
      view.setCenter(features[0].getGeometry().getCoordinates());
      view.setZoom(19);
      api.map.renderSync();
      return;
    }

    const extent = (window as any).ol.extent.createEmpty();
    features.forEach((feature: any) => (window as any).ol.extent.extend(extent, feature.getGeometry().getExtent()));
    view.fit(extent, { padding: [90, 90, 90, 90], maxZoom: 19 });
    api.map.renderSync();
  }, imageNames);
  await waitForMapRender(page);
}

async function selectFeatureByImageName(page: Page, imageName: string): Promise<void> {
  const featureId = await featureIdByImageName(page, imageName);
  await zoomToFeaturesByImageName(page, [imageName]);
  const clusters = await clusterSummaries(page);
  const cluster = clusters.find(candidate => candidate.ids.includes(featureId));
  expect(cluster, `Expected a rendered cluster for ${imageName}`).toBeTruthy();
  await clickCluster(page, cluster!);
  await expect.poll(() => selectedIds(page)).toContain(featureId);
}

async function shiftDragSelectAll(page: Page): Promise<void> {
  const pixels: number[][] = await page.evaluate(() => {
    const api = (window as any).PHOTO_ORGANIZER.locations.map;
    return api.vectorSource.getFeatures().map((feature: any) =>
      api.map.getPixelFromCoordinate(feature.getGeometry().getCoordinates()));
  });
  expect(pixels.length).toBeGreaterThan(0);
  const box = await page.locator('#map').boundingBox();
  expect(box).not.toBeNull();
  const xs = pixels.map(point => point[0]);
  const ys = pixels.map(point => point[1]);
  const pad = 44;
  const x1 = box!.x + Math.max(1, Math.min(...xs) - pad);
  const y1 = box!.y + Math.max(1, Math.min(...ys) - pad);
  const x2 = box!.x + Math.min(box!.width - 1, Math.max(...xs) + pad);
  const y2 = box!.y + Math.min(box!.height - 1, Math.max(...ys) + pad);

  await page.keyboard.down('Shift');
  await page.mouse.move(x1, y1);
  await page.mouse.down();
  await page.mouse.move(x2, y2, { steps: 8 });
  await page.mouse.up();
  await page.keyboard.up('Shift');
}

async function clickEmptyMap(page: Page): Promise<void> {
  const box = await page.locator('#map').boundingBox();
  expect(box).not.toBeNull();
  await page.mouse.click(box!.x + 8, box!.y + 8);
}

async function mockReverseGeocode(page: Page, name: string): Promise<void> {
  await page.route('**/locations/reverse-geocode', route => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({ success: true, location_name: name }),
  }));
}

async function clearAllLocationNames(page: Page): Promise<void> {
  await openMap(page);
  const ids = await allFeatureIds(page);
  const response = await page.request.post('/locations/bulk-update', {
    data: { media_item_ids: ids, clear: true },
    failOnStatusCode: false,
  });
  expect(response.ok()).toBe(true);
}

async function assignAllLocationNames(page: Page, name: string): Promise<void> {
  await openMap(page);
  const ids = await allFeatureIds(page);
  const response = await page.request.post('/locations/bulk-update', {
    data: { media_item_ids: ids, location_name: name },
    failOnStatusCode: false,
  });
  expect(response.ok()).toBe(true);
}

test.describe('Locations Map', () => {
  test.beforeEach(async ({ page }) => {
    await page.route('**/locations/reverse-geocode', route => route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ success: true, location_name: 'Mocked Location' }),
    }));
  });

  test.afterEach(async ({ page }) => {
    await page.unrouteAll({ behavior: 'ignoreErrors' });
  });

  test('locations_map_displays_photo_markers', async ({ page }) => {
    await openMap(page);

    await expect(page.locator('#map .ol-viewport canvas').first()).toBeVisible();
    const counts = await featureCounts(page);
    expect(counts.total).toBeGreaterThan(0);

    const clusters = await clusterSummaries(page);
    expect(clusters.length).toBeGreaterThan(0);

    const zoom = (await viewState(page)).zoom;
    expect(zoom).toBeGreaterThan(2);

    if (counts.total >= 2) {
      const visiblePoints = await page.evaluate(() => {
        const api = (window as any).PHOTO_ORGANIZER.locations.map;
        return api.vectorSource.getFeatures().map((feature: any) =>
          api.map.getPixelFromCoordinate(feature.getGeometry().getCoordinates()));
      });
      const uniqueRoundedPoints = new Set(visiblePoints.map((point: number[]) =>
        `${Math.round(point[0] / 10) * 10},${Math.round(point[1] / 10) * 10}`));
      expect(uniqueRoundedPoints.size).toBeGreaterThan(1);
    }
  });

  test('locations_selection_panel_shows_photo_details', async ({ page }) => {
    await openMap(page);
    const firstCluster = await clickFirstCluster(page);

    const panel = page.locator('#selection-panel');
    await expect(panel).toHaveClass(/active/);
    await expect(panel.locator('.preview-section')).toBeVisible();
    await expect(panel.locator('#photo-img')).toBeVisible();
    await expect(panel.locator('#mass-assign-btn')).toBeVisible();
    expect(await selectedIds(page)).toEqual(firstCluster.ids);

    if (firstCluster.total > 1) {
      await expect(panel.locator('#preview-photo-select')).toBeAttached();
      const firstName = await panel.locator('#photo-name').textContent();
      await panel.locator('.preview-thumb').nth(1).click();
      await expect(panel.locator('#photo-name')).not.toHaveText(firstName || '');
    }

    const preview = panel.locator('.preview-section');
    await panel.locator('.preview-toggle').click();
    await expect(preview).toHaveClass(/collapsed/);
    await panel.locator('.preview-toggle').click();
    await expect(preview).not.toHaveClass(/collapsed/);

    await page.evaluate(() => {
      const api = (window as any).PHOTO_ORGANIZER.locations.map;
      const view = api.map.getView();
      view.setZoom((view.getZoom() ?? 3) + 1);
      api.map.dispatchEvent('moveend');
    });
    await waitForMapRender(page);
    await expect.poll(() => selectedIds(page)).toEqual(firstCluster.ids);
    await expect(panel).toHaveClass(/active/);

    await shiftDragSelectAll(page);
    await expect.poll(async () => {
      const ids = await selectedIds(page);
      return ids.length >= firstCluster.ids.length && firstCluster.ids.every(id => ids.includes(id));
    }).toBe(true);

    await clickEmptyMap(page);
    await expect(panel).not.toHaveClass(/active/);
    expect(await selectedIds(page)).toEqual([]);

    await openMap(page);
    await clickFirstCluster(page);
    await expect(page.locator('#selection-panel')).toHaveClass(/active/);
    await page.locator('#selection-panel .selection-panel-close').click();
    await expect(page.locator('#selection-panel')).not.toHaveClass(/active/);
    expect(await selectedIds(page)).toEqual([]);
  });

  test('locations_partial_cluster_selection_indicator', async ({ page }) => {
    await openMap(page);
    const clusters = await clusterSummaries(page);
    const multiCluster = clusters.find(cluster => cluster.total > 1);

    test.skip(!multiCluster, 'Sandbox needs at least one multi-photo cluster for partial cluster rendering.');

    await page.evaluate((clusterIds: number[]) => {
      const api = (window as any).PHOTO_ORGANIZER.locations.map;
      api.selectedPhotoIds.clear();
      clusterIds.slice(0, Math.max(1, clusterIds.length - 1)).forEach(id => api.selectedPhotoIds.add(id));
      api.updateSelectionPanel();
      api.map.renderSync();
    }, multiCluster!.ids);

    const partial = (await clusterSummaries(page)).find(cluster =>
      cluster.ids.length === multiCluster!.ids.length && cluster.ids.every(id => multiCluster!.ids.includes(id)));
    expect(partial).toBeTruthy();
    expect(partial!.selected).toBeGreaterThan(0);
    expect(partial!.selected).toBeLessThan(partial!.total);
    expect(decodeURIComponent(partial!.iconSrc || '')).toContain('<path');
    await expect(page.locator('#selection-panel .cluster-summary-item')).toContainText(
      new RegExp(`${partial!.selected}\\s*/\\s*${partial!.total}`),
    );

    await clickCluster(page, partial!);
    await expect.poll(() => selectedIds(page)).toEqual(partial!.ids);

    const full = (await clusterSummaries(page)).find(cluster =>
      cluster.ids.length === partial!.ids.length && cluster.ids.every(id => partial!.ids.includes(id)));
    await clickCluster(page, full!);
    await expect.poll(() => selectedIds(page)).toEqual([]);
  });

  test('locations_filter_sidebar_filters_client_side', async ({ page }) => {
    await clearAllLocationNames(page);
    await openMap(page);
    const ids = await allFeatureIds(page);
    await page.request.post('/locations/bulk-update', {
      data: { media_item_ids: [ids[0]], location_name: 'Named Filter Fixture' },
    });
    await openMap(page);

    await clickFirstCluster(page);
    await expect(page.locator('#selection-panel')).toHaveClass(/active/);
    const beforeView = await viewState(page);
    const beforeUrl = page.url();
    const beforeCounts = await featureCounts(page);
    expect(beforeCounts.unnamed).toBeLessThan(beforeCounts.total);

    await page.locator('input[name="unnamed"]').check();
    await page.getByRole('button', { name: /Apply Filters/i }).click();

    expect(new URL(page.url()).pathname).toBe('/locations');
    expect(page.url()).toBe(beforeUrl);
    await expect.poll(() => featureCounts(page)).toEqual({
      total: beforeCounts.unnamed,
      unnamed: beforeCounts.unnamed,
    });
    expectSameView(await viewState(page), beforeView);
    expect(await selectedIds(page)).toEqual([]);
  });

  test('locations_clear_filters_client_side', async ({ page }) => {
    await clearAllLocationNames(page);
    await assignAllLocationNames(page, 'Clear Filter Fixture');
    await openMap(page);
    const allNamedCounts = await featureCounts(page);
    await page.locator('input[name="unnamed"]').check();
    await page.getByRole('button', { name: /Apply Filters/i }).click();
    await expect.poll(() => featureCounts(page)).toEqual({ total: 0, unnamed: 0 });

    const filteredUrl = page.url();
    const beforeClearView = await viewState(page);
    await page.getByRole('button', { name: /Clear Filters/i }).click();

    expect(page.url()).toBe(filteredUrl);
    await expect(page.locator('input[name="unnamed"]')).not.toBeChecked();
    await expect.poll(() => featureCounts(page)).toEqual(allNamedCounts);
    expectSameView(await viewState(page), beforeClearView);
  });

  test('locations_configure_filter_sidebar', async ({ page }) => {
    await openMap(page);
    await page.locator('#configure-filters-btn').click();
    const modal = page.locator('#configureFiltersModal');
    await expect(modal).toHaveClass(/active/);

    const yearRow = page.locator('.filter-config-row[data-key="year"]');
    await expect(yearRow).toBeVisible();
    const toggle = yearRow.locator('.filter-config-toggle');
    const originallyChecked = await toggle.isChecked();
    if (!originallyChecked) await toggle.check();
    await toggle.uncheck();

    await Promise.all([
      page.waitForResponse(response => response.url().includes('/settings/filters') && response.status() === 204),
      modal.locator('button[type="submit"]').click(),
    ]);
    await page.waitForLoadState('domcontentloaded');
    await expect(page.locator('#year-select')).toHaveCount(0);
    await expect(page.locator('#device-select')).toBeAttached();

    await page.locator('#configure-filters-btn').click();
    await page.locator('#filter-config-reset').click();
    await Promise.all([
      page.waitForResponse(response => response.url().includes('/settings/filters') && response.status() === 204),
      page.locator('#configureFiltersModal button[type="submit"]').click(),
    ]);
    await page.waitForLoadState('domcontentloaded');
    await expect(page.locator('#year-select')).toBeAttached();
  });

  test('locations_select_clusters_and_assign_name', async ({ page }) => {
    await mockReverseGeocode(page, 'Somewhere Else');
    await clearAllLocationNames(page);
    await openMap(page);

    const photoIds = await allFeatureIds(page);
    await shiftDragSelectAll(page);
    const panel = page.locator('#selection-panel');
    await expect(panel).toHaveClass(/active/);
    await expect(panel.locator('.mass-assignment-info')).toContainText(String(photoIds.length));
    await expect(panel.locator('.btn-recommended')).toBeVisible();

    await panel.locator('#mass-location-input').fill(TEST_LOCATION_NAME);
    await expect(panel.locator('#mass-location-input')).toHaveValue(TEST_LOCATION_NAME);
    const [response] = await Promise.all([
      page.waitForResponse(resp => resp.url().includes('/locations/bulk-update')),
      page.evaluate((locationName) => {
        const input = document.getElementById('mass-location-input') as HTMLInputElement | null;
        const button = document.getElementById('mass-assign-btn') as HTMLButtonElement | null;
        if (!input || !button) throw new Error('Mass assignment controls are not available');
        input.value = locationName;
        input.dispatchEvent(new Event('input', { bubbles: true }));
        button.click();
      }, TEST_LOCATION_NAME),
    ]);
    expect(response.ok()).toBe(true);
    await expect(page.locator('.notification.visible')).toBeVisible();
    await expect(panel).not.toHaveClass(/active/);
    expect(await selectedIds(page)).toEqual([]);

    const names = await page.evaluate(() =>
      (window as any).PHOTO_ORGANIZER.locations.map.vectorSource.getFeatures().map((feature: any) => ({
        featureName: feature.get('name'),
        payloadName: feature.get('item').name,
      })));
    expect(names.every(({ featureName, payloadName }: { featureName: string; payloadName: string }) =>
      featureName === TEST_LOCATION_NAME && payloadName === TEST_LOCATION_NAME)).toBe(true);

    const detailHtml = await (await page.request.get(`/media/view/${photoIds[0]}`)).text();
    expect(detailHtml).toContain(TEST_LOCATION_NAME);
  });

  test('locations_clear_location_names', async ({ page }) => {
    await mockReverseGeocode(page, 'Somewhere Else');
    await openMap(page);
    const photoIds = await allFeatureIds(page);
    await shiftDragSelectAll(page);
    const panel = page.locator('#selection-panel');
    await expect(panel).toHaveClass(/active/);

    const [response] = await Promise.all([
      page.waitForResponse(resp => resp.url().includes('/locations/bulk-update')),
      panel.locator('.btn-clear-names').click(),
    ]);
    expect(response.ok()).toBe(true);
    expect(response.request().postDataJSON()).toMatchObject({ clear: true });
    await expect(page.locator('.notification.visible')).toBeVisible();
    await expect(panel).not.toHaveClass(/active/);

    const counts = await featureCounts(page);
    expect(counts.unnamed).toBe(counts.total);

    await page.locator('input[name="unnamed"]').check();
    await page.getByRole('button', { name: /Apply Filters/i }).click();
    await expect.poll(() => featureCounts(page)).toEqual(counts);

    const detailHtml = await (await page.request.get(`/media/view/${photoIds[0]}`)).text();
    expect(detailHtml).not.toContain(TEST_LOCATION_NAME);
  });

  test('locations_recommends_existing_nearby_name', async ({ page }) => {
    await clearAllLocationNames(page);
    await openMap(page);
    const namedWhiteHouseId = await featureIdByImageName(page, NAMED_WHITE_HOUSE_IMAGE);
    const selectedWhiteHouseId = await featureIdByImageName(page, SELECTED_WHITE_HOUSE_IMAGE);
    expect(selectedWhiteHouseId).not.toBe(namedWhiteHouseId);

    const assignResponse = await page.request.post('/locations/bulk-update', {
      data: { media_item_ids: [namedWhiteHouseId], location_name: WHITE_HOUSE_LOCATION_NAME },
      failOnStatusCode: false,
    });
    expect(assignResponse.ok()).toBe(true);

    let reverseGeocodeCalls = 0;
    await page.route('**/locations/reverse-geocode', route => {
      reverseGeocodeCalls += 1;
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ success: true, location_name: CHICAGO_GEOCODE_NAME }),
      });
    });

    await openMap(page);
    await zoomToFeaturesByImageName(page, [NAMED_WHITE_HOUSE_IMAGE, SELECTED_WHITE_HOUSE_IMAGE]);
    await selectFeatureByImageName(page, SELECTED_WHITE_HOUSE_IMAGE);
    expect(await selectedIds(page)).toEqual([selectedWhiteHouseId]);

    const recommended = page.locator('#selection-panel .btn-recommended');
    await expect(recommended).toHaveCount(1);
    await expect(recommended).toContainText(WHITE_HOUSE_LOCATION_NAME);
    expect(reverseGeocodeCalls).toBe(0);

    await selectFeatureByImageName(page, CHICAGO_IMAGE);
    await expect(recommended).toHaveCount(1);
    await expect(recommended).toContainText(CHICAGO_GEOCODE_NAME);
    await expect(recommended).not.toContainText(WHITE_HOUSE_LOCATION_NAME);
    expect(reverseGeocodeCalls).toBe(1);
  });

  test('locations_reverse_geocode_suggests_name', async ({ page }) => {
    await clearAllLocationNames(page);
    const suggested = 'Mocked Beach Town';
    let geocodeCalls = 0;
    let geocodedBody: { lat?: number; lon?: number } = {};
    await page.route('**/locations/reverse-geocode', route => {
      geocodeCalls += 1;
      geocodedBody = route.request().postDataJSON();
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ success: true, location_name: suggested }),
      });
    });

    await openMap(page);
    await clickFirstCluster(page);

    const recommended = page.locator('#selection-panel .btn-recommended');
    await expect(recommended).toHaveCount(1);
    await expect(recommended).toContainText(suggested);

    const selectedCoordinate = await page.evaluate(() => {
      const api = (window as any).PHOTO_ORGANIZER.locations.map;
      const selected = api.vectorSource.getFeatures().filter((feature: any) => api.selectedPhotoIds.has(feature.get('id')));
      const coordinates = selected.map((feature: any) => {
        const coords = (window as any).ol.proj.toLonLat(feature.getGeometry().getCoordinates());
        return { lat: coords[1], lon: coords[0] };
      });
      const centroid = coordinates.reduce((total: any, point: any) => ({
        lat: total.lat + point.lat / coordinates.length,
        lon: total.lon + point.lon / coordinates.length,
      }), { lat: 0, lon: 0 });
      return coordinates.reduce((closest: any, point: any) =>
        Math.hypot(point.lat - centroid.lat, point.lon - centroid.lon) <
        Math.hypot(closest.lat - centroid.lat, closest.lon - centroid.lon) ? point : closest, coordinates[0]);
    });
    expect(Math.abs(geocodedBody.lat! - selectedCoordinate.lat)).toBeLessThan(0.01);
    expect(Math.abs(geocodedBody.lon! - selectedCoordinate.lon)).toBeLessThan(0.01);

    await page.evaluate(() => (window as any).PHOTO_ORGANIZER.locations.map.updateSelectionPanel());
    await waitForMapRender(page);
    await expect(page.locator('#selection-panel .btn-recommended')).toHaveCount(1);
    expect(geocodeCalls).toBe(1);

    await clickEmptyMap(page);
    await clickFirstCluster(page);
    await expect(page.locator('#selection-panel .btn-recommended')).toHaveCount(1);
    expect(geocodeCalls).toBe(1);
  });

  test('locations_reverse_geocode_rate_limit_fallback', async ({ page }) => {
    await clearAllLocationNames(page);
    let geocodeCalls = 0;
    await page.route('**/locations/reverse-geocode', route => {
      geocodeCalls += 1;
      return route.fulfill({
        status: 429,
        contentType: 'application/json',
        body: JSON.stringify({ error: 'rate limited', code: 'reverse_geocode_rate_limited' }),
      });
    });

    await openMap(page);
    await clickFirstCluster(page);
    const panel = page.locator('#selection-panel');
    await expect(panel).toHaveClass(/active/);
    await expect(panel.locator('.btn-recommended')).toHaveCount(0);
    await expect(panel.locator('#mass-location-input')).toBeVisible();
    await expect(panel.locator('#mass-assign-btn')).toBeVisible();
    await expect(panel.locator('.btn-clear-names')).toBeVisible();

    await page.evaluate(() => (window as any).PHOTO_ORGANIZER.locations.map.updateSelectionPanel());
    await page.waitForTimeout(100);
    await expect(panel.locator('.btn-recommended')).toHaveCount(0);
    expect(geocodeCalls).toBe(1);
  });
});
