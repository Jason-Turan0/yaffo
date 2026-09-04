import { test, expect, Page } from '@playwright/test';
import {
  VIEWPORTS,
  expectFitsViewport,
  expectNoPageOverflow,
  expectPanelContract,
  withTouchContext,
} from '../_support/responsive';

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
const CHICAGO_LOCATION_NAME = 'Chicago Weekend';
const NAMED_CHICAGO_IMAGE = '2015-10-09_103400_chicago-riverwalk.png';
const SELECTED_CHICAGO_IMAGE = '2015-10-11_085600_family-breakfast.png';
const DISTANT_IMAGE = '2021-07-10_101800_beach-arrival.png';
const DISTANT_GEOCODE_NAME = 'Mocked Siesta Key Beach';

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

/** Post a bulk-update to the server using the browser's own fetch so the
 *  Content-Type and body serialisation match what the server expects. */
async function bulkUpdate(
  page: Page,
  body: { media_item_ids: number[]; location_name?: string; clear?: boolean },
): Promise<boolean> {
  return page.evaluate(async (payload) => {
    const response = await fetch('/locations/bulk-update', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    return response.ok;
  }, body);
}

/**
 * OpenLayers caches its viewport size, so a canvas that is never told about a
 * layout change keeps rendering at the old width — visibly correct markup with
 * a stale map inside it. Every layout transition on this page (panel open or
 * close, breakpoint crossing, rotation) has to end with the map's own size
 * matching the element it is rendered into.
 */
async function mapSizeMatchesContainer(page: Page): Promise<boolean> {
  return page.evaluate(() => {
    const api = (window as any).PHOTO_ORGANIZER?.locations?.map;
    const element = document.getElementById('map');
    const size = api?.map?.getSize?.();
    if (!element || !Array.isArray(size)) return false;
    return Math.abs(size[0] - element.clientWidth) <= 1
      && Math.abs(size[1] - element.clientHeight) <= 1;
  });
}

async function expectMapSizedToContainer(page: Page): Promise<void> {
  await expect
    .poll(() => mapSizeMatchesContainer(page), {
      message: 'OpenLayers was not told its new size after the layout changed',
    })
    .toBe(true);
}

/** The map keeps the whole row: it is the page's primary narrow-screen surface. */
async function expectMapSpansTheLayout(page: Page): Promise<void> {
  const widths = await page.evaluate(() => ({
    map: document.getElementById('map')!.clientWidth,
    layout: document.querySelector<HTMLElement>('.map-layout')!.clientWidth,
  }));
  expect(Math.abs(widths.map - widths.layout), 'the map does not span the layout row')
    .toBeLessThanOrEqual(1);
}

/**
 * A resize must never cost the user their work, so these tests have to prove
 * the page was not quietly reloaded underneath them. A marker on `window`
 * disappears with the document.
 */
async function markPageInstance(page: Page): Promise<void> {
  await page.evaluate(() => { (window as any).__locationsInstance = 'kept'; });
}

async function expectSamePageInstance(page: Page): Promise<void> {
  expect(
    await page.evaluate(() => (window as any).__locationsInstance),
    'the page reloaded instead of adapting in place',
  ).toBe('kept');
}

async function panelGeometry(page: Page) {
  return page.evaluate(() => {
    const panel = document.getElementById('selection-panel')!;
    const rect = panel.getBoundingClientRect();
    const style = getComputedStyle(panel);
    const content = document.getElementById('selection-panel-content')!;
    return {
      position: style.position,
      top: rect.top,
      left: rect.left,
      right: rect.right,
      bottom: rect.bottom,
      width: rect.width,
      contentOverflowY: getComputedStyle(content).overflowY,
      contentOverscroll: getComputedStyle(content).overscrollBehaviorY,
      contentScrollHeight: content.scrollHeight,
      contentClientHeight: content.clientHeight,
      viewportWidth: document.documentElement.clientWidth,
      viewportHeight: window.innerHeight,
    };
  });
}

async function clearAllLocationNames(page: Page): Promise<void> {
  await openMap(page);
  const ids = await allFeatureIds(page);
  const ok = await bulkUpdate(page, { media_item_ids: ids, clear: true });
  expect(ok, 'Failed to clear all location names via bulk-update').toBe(true);
}

async function assignAllLocationNames(page: Page, name: string): Promise<void> {
  await openMap(page);
  const ids = await allFeatureIds(page);
  const ok = await bulkUpdate(page, { media_item_ids: ids, location_name: name });
  expect(ok, `Failed to assign "${name}" to all photos via bulk-update`).toBe(true);
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

    // Wait for the CSS transition on the selection panel and map to complete
    // before reading pixel coordinates. The panel opens with a 300 ms
    // transition (.selection-panel.active, #map { transition: flex 0.3s ease })
    // and clusterSummaries reads getPixelFromCoordinate whose output depends on
    // the current map viewport size.
    await page.waitForTimeout(400);

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
    await bulkUpdate(page, { media_item_ids: [ids[0]], location_name: 'Named Filter Fixture' });
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

    // Set the value and submit in one browser task. With a larger clustered
    // fixture, a late recommendation refresh can otherwise clear the input
    // between a separate fill and click.
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
    const namedChicagoId = await featureIdByImageName(page, NAMED_CHICAGO_IMAGE);
    const selectedChicagoId = await featureIdByImageName(page, SELECTED_CHICAGO_IMAGE);
    expect(selectedChicagoId).not.toBe(namedChicagoId);

    const ok = await bulkUpdate(page, { media_item_ids: [namedChicagoId], location_name: CHICAGO_LOCATION_NAME });
    expect(ok, 'Failed to assign nearby Chicago name').toBe(true);

    let reverseGeocodeCalls = 0;
    await page.route('**/locations/reverse-geocode', route => {
      reverseGeocodeCalls += 1;
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ success: true, location_name: DISTANT_GEOCODE_NAME }),
      });
    });

    await openMap(page);
    await zoomToFeaturesByImageName(page, [NAMED_CHICAGO_IMAGE, SELECTED_CHICAGO_IMAGE]);
    await selectFeatureByImageName(page, SELECTED_CHICAGO_IMAGE);
    expect(await selectedIds(page)).toEqual([selectedChicagoId]);

    const recommended = page.locator('#selection-panel .btn-recommended');
    await expect(recommended).toHaveCount(1);
    await expect(recommended).toContainText(CHICAGO_LOCATION_NAME);
    expect(reverseGeocodeCalls).toBe(0);

    await selectFeatureByImageName(page, DISTANT_IMAGE);
    await expect(recommended).toHaveCount(1);
    await expect(recommended).toContainText(DISTANT_GEOCODE_NAME);
    await expect(recommended).not.toContainText(CHICAGO_LOCATION_NAME);
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

  // ---------------------------------------------------------------------------
  // Responsive coverage (P4 — locations). docs/development/responsive.md, Phase 4
  // item 1: the map is the primary narrow-screen surface, the selected-cluster
  // details are a centered modal, hover-only affordances have a
  // coarse-pointer path, OpenLayers is resized after every layout transition, and
  // map/selection/unsaved state survives a resize through the breakpoint.
  // Shared contract assertions come from ../_support/responsive.
  // ---------------------------------------------------------------------------

  test('locations_route_fits_every_contract_viewport', async ({ page }) => {
    for (const [name, viewport] of Object.entries(VIEWPORTS)) {
      await page.setViewportSize(viewport);
      await openMap(page);
      await expectNoPageOverflow(page);
      await expectMapSizedToContainer(page);
      // The map is the point of the page: it must not be squeezed to a strip on
      // a short landscape phone by the desktop's fixed `100vh - 200px`.
      expect(
        await page.evaluate(() => document.getElementById('map')!.clientHeight),
        `the map collapsed at ${name}`,
      ).toBeGreaterThanOrEqual(240);
    }
  });

  test('locations_filter_panel_uses_a_peer_navbar_panel', async ({ page }) => {
    await expectPanelContract(page, { route: '/locations', panelId: 'locations-filters' });

    await page.setViewportSize(VIEWPORTS.narrow);
    await page.goto('/locations');
    const toggle = page.locator('#locations-filters-toggle');
    const panel = page.locator('#locations-filters');
    const badge = toggle.locator('[data-nav-panel-count]');

    // The applied-filter badge is server-rendered, so it is already correct on
    // first paint rather than popping in after hydration.
    await expect(badge).toBeHidden();

    await toggle.click();
    await expect(panel).toBeVisible();
    await page.locator('input[name="unnamed"]').check();

    // Escape belongs to the topmost surface; with no dialog open that is the panel.
    await page.keyboard.press('Escape');
    await expect(toggle).toHaveAttribute('aria-expanded', 'false');
    await expect(panel).toBeHidden();

    // Reopening shows the same live DOM, so the tick is still there.
    await toggle.click();
    await expect(page.locator('input[name="unnamed"]')).toBeChecked();

    await page.setViewportSize(VIEWPORTS.desktop);
    await expect(toggle).toBeHidden();
    await expect(page.locator('input[name="unnamed"]')).toBeChecked();
    await expectNoPageOverflow(page);
  });

  test('locations_narrow_filters_apply_and_clear_without_leaving_the_map', async ({ page }) => {
    await clearAllLocationNames(page);
    await page.setViewportSize(VIEWPORTS.narrow);
    await openMap(page);
    const ids = await allFeatureIds(page);
    expect(await bulkUpdate(page, { media_item_ids: [ids[0]], location_name: 'Narrow Filter Fixture' })).toBe(true);
    await openMap(page);
    await markPageInstance(page);

    const before = await featureCounts(page);
    const beforeUrl = page.url();
    const beforeView = await viewState(page);

    // Applying does not necessarily close the panel, so open it by state rather
    // than by toggling it and hoping.
    const openFilters = async () => {
      if (await page.locator('#locations-filters-toggle').getAttribute('aria-expanded') !== 'true') {
        await page.locator('#locations-filters-toggle').click();
      }
      await expect(page.locator('#locations-filters')).toBeVisible();
    };

    await openFilters();
    await page.locator('input[name="unnamed"]').check();
    await page.getByRole('button', { name: /Apply Filters/i }).click();

    // The form is client-side on this page: applying from inside the navbar
    // panel filters the markers in place instead of navigating.
    expect(page.url()).toBe(beforeUrl);
    await expectSamePageInstance(page);
    await expect.poll(() => featureCounts(page)).toEqual({
      total: before.unnamed,
      unnamed: before.unnamed,
    });
    expectSameView(await viewState(page), beforeView);
    await expectNoPageOverflow(page);

    await openFilters();
    await page.getByRole('button', { name: /Clear Filters/i }).click();
    expect(page.url()).toBe(beforeUrl);
    await expectSamePageInstance(page);
    await expect.poll(() => featureCounts(page)).toEqual(before);

    await clearAllLocationNames(page);
  });

  test('locations_selection_details_open_in_a_centered_modal', async ({ page }) => {
    await page.setViewportSize(VIEWPORTS.narrow);
    await openMap(page);
    await selectFeatureByImageName(page, DISTANT_IMAGE);

    const panel = page.locator('#selection-panel');
    await expect(panel).toHaveClass(/active/);
    await expect(panel).toBeVisible();

    const narrow = await panelGeometry(page);
    expect(narrow.position, 'the narrow selection panel is not viewport-bound').toBe('fixed');
    expect(narrow.left, 'the modal has no leading-edge gutter').toBeGreaterThan(0);
    expect(narrow.right, 'the modal has no trailing-edge gutter').toBeLessThan(narrow.viewportWidth);
    expect(Math.abs((narrow.left + narrow.right) / 2 - narrow.viewportWidth / 2), 'the modal is not horizontally centered')
      .toBeLessThanOrEqual(1);
    expect(Math.abs((narrow.top + narrow.bottom) / 2 - narrow.viewportHeight / 2), 'the modal is not vertically centered')
      .toBeLessThanOrEqual(1);
    await expect(panel).toHaveAttribute('role', 'dialog');
    await expect(panel).toHaveAttribute('aria-modal', 'true');
    await expect(page.locator('#selection-panel-backdrop')).toBeVisible();

    await expectFitsViewport(page, '#selection-panel');
    await expectNoPageOverflow(page);
    // The map keeps the whole row beneath the modal: it stays the primary surface
    // instead of being squeezed into the leftover of a 380 px side column.
    await expectMapSpansTheLayout(page);
    await expectMapSizedToContainer(page);

    // Above the boundary the same live panel is the map's right-hand column again.
    const selectedBefore = await selectedIds(page);
    await markPageInstance(page);
    await page.setViewportSize(VIEWPORTS.desktop);
    await expectMapSizedToContainer(page);
    await expectSamePageInstance(page);
    expect(await selectedIds(page)).toEqual(selectedBefore);

    expect((await panelGeometry(page)).position).toBe('static');
    // The desktop panel animates back to its column, so both boxes have to be
    // read in the same frame or the comparison lands mid-transition.
    await expect
      .poll(() => page.evaluate(() => {
        const panel = document.getElementById('selection-panel')!.getBoundingClientRect();
        const map = document.getElementById('map')!.getBoundingClientRect();
        return panel.left >= map.right - 1;
      }), { message: 'the desktop panel never settled beside the map' })
      .toBe(true);
  });

  test('locations_selection_modal_contains_its_own_overflow', async ({ page }) => {
    await page.setViewportSize(VIEWPORTS.minimum);
    await openMap(page);
    await shiftDragSelectAll(page);
    await expect(page.locator('#selection-panel')).toHaveClass(/active/);

    const geometry = await panelGeometry(page);
    expect(geometry.contentOverflowY, 'the modal body is not the scroll region').toBe('auto');
    expect(geometry.contentOverscroll, 'the modal chains its overscroll to the document').toBe('contain');
    expect(geometry.contentScrollHeight, 'not enough content to prove the modal contains it')
      .toBeGreaterThan(geometry.contentClientHeight);

    const scrolled = await page.evaluate(() => {
      const content = document.getElementById('selection-panel-content')!;
      const documentTopBefore = document.scrollingElement!.scrollTop;
      content.scrollTop = content.scrollHeight;
      return {
        panelScrollTop: content.scrollTop,
        documentTopBefore,
        documentTopAfter: document.scrollingElement!.scrollTop,
      };
    });
    expect(scrolled.panelScrollTop, 'the modal body did not scroll').toBeGreaterThan(0);
    expect(scrolled.documentTopAfter, 'scrolling the modal moved the document')
      .toBe(scrolled.documentTopBefore);

    await expectNoPageOverflow(page);
    await expectFitsViewport(page, '#selection-panel');
  });

  test('locations_map_follows_narrow_container_resizes', async ({ page }) => {
    await page.setViewportSize(VIEWPORTS.tabletPortrait);
    await openMap(page);
    await expectMapSizedToContainer(page);
    await markPageInstance(page);

    // Breakpoint change: the sidebar leaves the row and the panel leaves the flow.
    await page.setViewportSize(VIEWPORTS.narrow);
    await expectMapSizedToContainer(page);
    await expectSamePageInstance(page);
    await waitForMapRender(page);

    // Panel open.
    await selectFeatureByImageName(page, DISTANT_IMAGE);
    await expect(page.locator('#selection-panel')).toHaveClass(/active/);
    await expectMapSizedToContainer(page);
    await expectNoPageOverflow(page);
    await expectFitsViewport(page, '#selection-panel');

    // Rotation, with the modal still open.
    await page.setViewportSize(VIEWPORTS.narrowLandscape);
    await expectMapSizedToContainer(page);
    await expectNoPageOverflow(page);
    await expectFitsViewport(page, '#selection-panel');

    // Panel close.
    await page.locator('.selection-panel-close').click();
    await expect(page.locator('#selection-panel')).not.toHaveClass(/active/);
    await expectMapSizedToContainer(page);
    await expectSamePageInstance(page);
  });

  test('locations_map_state_survives_a_resize_through_the_breakpoint', async ({ page }) => {
    await page.setViewportSize(VIEWPORTS.desktop);
    await openMap(page);
    await zoomToFeaturesByImageName(page, [DISTANT_IMAGE]);
    const before = await viewState(page);
    await markPageInstance(page);

    await page.setViewportSize(VIEWPORTS.narrow);
    await expectMapSizedToContainer(page);
    await expectSamePageInstance(page);
    expectSameView(await viewState(page), before);

    await page.setViewportSize(VIEWPORTS.narrowLandscape);
    await expectMapSizedToContainer(page);
    expectSameView(await viewState(page), before);

    await page.setViewportSize(VIEWPORTS.desktop);
    await expectMapSizedToContainer(page);
    await expectSamePageInstance(page);
    expectSameView(await viewState(page), before);
  });

  test('locations_unsaved_assignment_survives_a_resize_through_the_breakpoint', async ({ page }) => {
    await clearAllLocationNames(page);
    await page.setViewportSize(VIEWPORTS.desktop);
    await openMap(page);
    await selectFeatureByImageName(page, DISTANT_IMAGE);
    // The recommendation lands late and re-renders part of the panel; wait for it
    // so the assertions below are about the resize and not that race.
    await expect(page.locator('#selection-panel .btn-recommended')).toHaveCount(1);

    const pending = 'Half typed harbour';
    await page.locator('#mass-location-input').fill(pending);
    const selectedBefore = await selectedIds(page);
    const viewBefore = await viewState(page);
    await markPageInstance(page);

    await page.setViewportSize(VIEWPORTS.narrow);
    await expectMapSizedToContainer(page);
    await expectSamePageInstance(page);
    await expect(page.locator('#selection-panel')).toHaveClass(/active/);
    await expect(page.locator('#mass-location-input')).toHaveValue(pending);
    expect(await selectedIds(page)).toEqual(selectedBefore);
    expectSameView(await viewState(page), viewBefore);

    // The panel is rebuilt from scratch on every map move; unsaved work is not
    // the renderer's to throw away.
    await page.evaluate(() => (window as any).PHOTO_ORGANIZER.locations.map.updateSelectionPanel());
    await expect(page.locator('#mass-location-input')).toHaveValue(pending);

    await page.setViewportSize(VIEWPORTS.desktop);
    await expectMapSizedToContainer(page);
    await expectSamePageInstance(page);
    await expect(page.locator('#mass-location-input')).toHaveValue(pending);

    // Dropping the selection is an explicit discard, so the draft goes with it.
    await page.locator('.btn-clear-selection').click();
    await expect(page.locator('#selection-panel')).not.toHaveClass(/active/);
    await selectFeatureByImageName(page, DISTANT_IMAGE);
    await expect(page.locator('#mass-location-input')).toHaveValue('');
  });

  test('locations_long_location_names_do_not_widen_the_narrow_page', async ({ page }) => {
    await clearAllLocationNames(page);
    await page.setViewportSize(VIEWPORTS.minimum);
    await openMap(page);
    const ids = await allFeatureIds(page);
    const longName = 'Llanfairpwllgwyngyllgogerychwyrndrobwllllantysiliogogogoch Observation Deck';
    expect(await bulkUpdate(page, { media_item_ids: ids, location_name: longName })).toBe(true);

    await openMap(page);
    await shiftDragSelectAll(page);
    await expect(page.locator('#selection-panel')).toHaveClass(/active/);
    await expect(page.locator('.btn-quick-assign').first()).toBeVisible();

    // The quick-assign chip, the cluster summary and the modal itself all have to
    // absorb an unbreakable name rather than pushing the document sideways.
    await expectNoPageOverflow(page);
    await expectFitsViewport(page, '#selection-panel');
    const chipWidth = (await page.locator('.btn-quick-assign').first().boundingBox())!.width;
    expect(chipWidth).toBeLessThanOrEqual(VIEWPORTS.minimum.width);

    await clearAllLocationNames(page);
  });

  test('locations_touch_reaches_every_hover_only_affordance', async ({ browser }) => {
    await withTouchContext(browser, VIEWPORTS.narrow, async (page) => {
      await mockReverseGeocode(page, 'Mocked Location');
      await openMap(page);
      const ids = await allFeatureIds(page);
      // The desktop layout answers a long name with an ellipsis plus a `title`
      // tooltip — information a touch device can never reveal.
      const longName = 'Sankt Leonhard in Passeier lookout, Provincia autonoma di Bolzano';
      expect(await bulkUpdate(page, { media_item_ids: ids, location_name: longName })).toBe(true);

      await openMap(page);
      const cluster = await clickFirstCluster(page);
      expect(cluster.total, 'need a multi-photo cluster to exercise the thumbnails')
        .toBeGreaterThan(1);
      await expect(page.locator('#selection-panel')).toHaveClass(/active/);

      // 1. The quick-assign chip states the whole name instead of hiding it in `title`.
      const quickAssign = page.locator('.btn-quick-assign').first();
      await expect(quickAssign).toHaveAttribute('title', new RegExp(longName));
      const chip = await quickAssign.evaluate(element => ({
        clipped: element.scrollWidth - element.clientWidth,
        whiteSpace: getComputedStyle(element).whiteSpace,
      }));
      expect(chip.whiteSpace, 'the chip still ellipsises a name touch cannot reveal').toBe('normal');
      expect(chip.clipped, 'the chip clips the name it would assign').toBeLessThanOrEqual(1);

      // 2. A thumbnail carries its filename only in `title`; tapping it puts that
      //    name on screen, which is the coarse-pointer path to the same fact.
      const secondThumb = page.locator('.preview-thumb').nth(1);
      const thumbTitle = await secondThumb.getAttribute('title');
      expect(thumbTitle).toBeTruthy();
      const thumbBox = await secondThumb.boundingBox();
      expect(thumbBox!.width).toBeGreaterThanOrEqual(44);
      expect(thumbBox!.height).toBeGreaterThanOrEqual(44);
      await secondThumb.tap();
      await expect(page.locator('#photo-name')).toHaveText(thumbTitle!);

      // 3. Collapsing the preview is a tap, not a hover.
      await page.locator('.preview-toggle').tap();
      await expect(page.locator('.preview-section')).toHaveClass(/collapsed/);
      await expect(page.locator('.preview-body')).toBeHidden();

      // 4. Dismissing the modal is a real 44 px target, not a mouse-sized ×.
      const close = page.locator('.selection-panel-close');
      const closeBox = await close.boundingBox();
      expect(closeBox!.width).toBeGreaterThanOrEqual(44);
      expect(closeBox!.height).toBeGreaterThanOrEqual(44);
      await close.tap();
      await expect(page.locator('#selection-panel')).not.toHaveClass(/active/);

      expect(await bulkUpdate(page, { media_item_ids: ids, clear: true })).toBe(true);
    });
  });
});
