import { VIEWPORTS, expectNoPageOverflow, expectFitsViewport, expectPanelContract, withTouchContext } from '../_support/responsive';
import { test, expect, Page } from '@playwright/test';
import path from 'node:path';
import { copyPhotoWithUniqueMarker, findAnyPhotoIn, findFileWithContentIn, removeTempFile } from '../_support/sandbox-fs';

const UNIQ = Date.now();
const SPEC_FILE_STEM = `spec-index-${UNIQ}`;
const PERSON_NAME = `Spec Thumbnail ${UNIQ}`;

// The suite drops/removes real files in the media and thumbnail directories and
// fixes the library around them, so it runs serially and cleans back to the
// in-sync baseline. The Flask server and this test process share a filesystem
// (local isolated environment); file setup goes through the _support helpers.
test.describe.configure({ mode: 'serial' });

let mediaDir: string;
const droppedFiles: string[] = [];
let personId: number | null = null;

async function readMediaDir(page: Page): Promise<string> {
  await page.goto('/settings');
  const dir = (await page.locator('.media-dir-item .media-dir-path').first().textContent())!.trim();
  expect(dir.length).toBeGreaterThan(0);
  return dir;
}

async function readThumbnailDir(page: Page): Promise<string> {
  await page.goto('/settings');
  const dir = (await page.locator('#current-thumbnail-dir').textContent())!.trim();
  expect(dir.length).toBeGreaterThan(0);
  return dir;
}

async function openLibraryHealth(page: Page): Promise<void> {
  await page.goto('/library/health');
  await expect(page.locator('.page-header')).toContainText('Library Health');
}

// The scan streams NDJSON; the final record fills every stat. Wait for the
// counters to leave their '—' placeholder (Missing Thumbnails keeps '—' only when
// the thumbnail folder can't be checked, which never happens in the sandbox).
async function waitForScanDone(page: Page): Promise<void> {
  for (const stat of ['stat-total-filesystem', 'stat-total-imported', 'stat-total-indexed', 'stat-unindexed',
    'stat-orphaned', 'stat-missing-thumbnails']) {
    await expect(page.locator(`#${stat}`)).not.toHaveText('—', { timeout: 20_000 });
  }
}

async function statValue(page: Page, id: string): Promise<number> {
  return Number((await page.locator(`#${id}`).textContent())!.replace(/[^\d]/g, ''));
}

async function openLibraryHealthScanned(page: Page): Promise<void> {
  await openLibraryHealth(page);
  await waitForScanDone(page);
}

// A card's file list folds under "Show files".
async function showFiles(page: Page, key: string): Promise<void> {
  const details = page.locator(`#issue-${key} details`);
  if (!(await details.evaluate(el => (el as HTMLDetailsElement).open))) {
    await details.locator('summary').click();
  }
}

function dropNewPhoto(label: string): string {
  const source = findAnyPhotoIn(mediaDir);
  // Keep the source extension: the primary fixture is PNG, the peer's JPEG.
  const filename = `${SPEC_FILE_STEM}-${label}${path.extname(source).toLowerCase()}`;
  const file = path.join(mediaDir, filename);
  copyPhotoWithUniqueMarker(source, file, `spec-${UNIQ}-${label}`);
  droppedFiles.push(file);
  return filename;
}

function deleteDroppedPhoto(filename: string): void {
  const file = droppedFiles.find(f => path.basename(f) === filename)!;
  removeTempFile(file);
  droppedFiles.splice(droppedFiles.indexOf(file), 1);
}

// Click a card's fix and wait for its 202. Buttons stay disabled while another
// suite's import/index job runs on the shared worker, so reload until enabled.
async function clickFix(page: Page, buttonId: string, endpoint: string): Promise<void> {
  await expect(async () => {
    await openLibraryHealthScanned(page);
    await expect(page.locator(`#${buttonId}`)).toBeEnabled({ timeout: 1000 });
  }).toPass({ timeout: 10_000, intervals: [1_000] });
  await Promise.all([
    page.waitForResponse(response => response.url().includes(endpoint) && response.status() === 202),
    page.locator(`#${buttonId}`).click(),
  ]);
}

// Reload until the stat reaches zero: the fix runs as a background job (an import
// runs face detection + classification). Each test does one fix so this wait fits
// the 30-second test budget.
async function waitForStatZero(page: Page, stat: string): Promise<void> {
  await expect(async () => {
    await openLibraryHealthScanned(page);
    expect(await statValue(page, stat)).toBe(0);
  }).toPass({ timeout: 20_000, intervals: [1_000] });
}

// On a settled load, only the all-clear shows in Library status.
async function expectInSync(page: Page): Promise<void> {
  await openLibraryHealthScanned(page);
  await expect(page.locator('#status-in-sync')).toBeVisible();
  await expect(page.locator('#status-in-sync')).toContainText('Everything is in sync');
  await expect(page.locator('#scan-results .issue-card:visible')).toHaveCount(0);
}

// People setup and cleanup go through the same JSON endpoints the app's own pages
// call (fetch carries the CSRF token via security.js).
async function createPersonViaApi(page: Page, name: string): Promise<number> {
  await page.goto('/people');
  const result = await page.evaluate(async (personName) => {
    const response = await fetch('/api/people/create', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: personName }),
    });
    return { ok: response.ok, status: response.status, body: await response.json().catch(() => null) };
  }, name);
  expect(result.ok, `Failed to create person: HTTP ${result.status}`).toBeTruthy();
  return result.body.person_id as number;
}

async function assignFaceViaApi(page: Page, faceId: number, id: number): Promise<void> {
  const result = await page.evaluate(async (params) => {
    const response = await fetch('/api/faces/assign', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ faces: [params.faceId], person: params.id, faceStatus: 'ASSIGNED' }),
    });
    return { ok: response.ok, status: response.status };
  }, { faceId, id });
  expect(result.ok, `Failed to assign face ${faceId}: HTTP ${result.status}`).toBeTruthy();
}

// The tail of the unassigned pool, skipping the similarity cluster the parallel
// face_assignment suite works through.
async function pickUnassignedFaceId(page: Page): Promise<number> {
  await page.goto('/faces?group_by=similarity&threshold=2');
  const clusterJson = await page.locator('.suggestion-group').first().getAttribute('data-faces').catch(() => null);
  const reserved = new Set(clusterJson ? (JSON.parse(clusterJson) as { id: number }[]).map(f => f.id) : []);
  await page.goto('/faces?group_by=people&threshold=100');
  const ids: number[] = [];
  for (const json of await page.locator('.suggestion-group').evaluateAll(
    els => els.map(el => el.getAttribute('data-faces')))) {
    if (json) ids.push(...(JSON.parse(json) as { id: number }[]).map(f => f.id));
  }
  const candidates = ids.filter(id => !reserved.has(id));
  expect(candidates.length, 'expected an unassigned face').toBeGreaterThan(0);
  return candidates[candidates.length - 1];
}

test.describe('Library Health', () => {
  test.afterAll(async ({ browser }) => {
    // If a test failed mid-flight, remove what it dropped so the next fix returns
    // the sandbox to baseline, and delete the person it created.
    for (const file of droppedFiles) removeTempFile(file);
    if (personId !== null) {
      const page = await browser.newPage();
      await page.goto('/people');
      await page.evaluate(async (id) => { await fetch(`/people/${id}/delete`, { method: 'POST' }); }, personId);
      await page.close();
    }
  });

  test('index_photos_scan_shows_stats', async ({ page }) => {
    mediaDir = await readMediaDir(page);

    await openLibraryHealthScanned(page);

    // Every counter is populated after the scan. (Per-tick live updates of "Total on
    // Filesystem" aren't reliably observable on a small library — the stream
    // finishes in one beat — so the populated end state is the asserted contract.)
    expect(await statValue(page, 'stat-total-filesystem')).toBeGreaterThan(0);
    expect(await statValue(page, 'stat-total-imported')).toBeGreaterThan(0);
    expect(await statValue(page, 'stat-total-indexed')).toBeGreaterThan(0);

    // Library status shows either the all-clear alone, or a card per problem.
    const cards = page.locator('#scan-results .issue-card:visible');
    const problems = await statValue(page, 'stat-unindexed') + await statValue(page, 'stat-orphaned')
      + await statValue(page, 'stat-missing-thumbnails');
    if (problems === 0 && await cards.count() === 0) {
      await expect(page.locator('#status-in-sync')).toContainText('Everything is in sync');
    } else {
      await expect(page.locator('#status-in-sync')).toBeHidden();
      await expect(cards.first().locator('.issue-card-title')).not.toBeEmpty();
    }
  });

  // The two cards' fixes, one job per test: a new photo is indexed; then with a new
  // and a deleted photo both cards show, Index them leaves the orphan alone, and
  // Remove them clears it; finally the sandbox returns to baseline.
  let firstPhoto = '';
  let secondPhoto = '';

  test('index_photos_index_new_photo', async ({ page }) => {
    mediaDir ??= await readMediaDir(page);
    firstPhoto = dropNewPhoto('a');
    await openLibraryHealthScanned(page);
    await expect(page.locator('#issue-unindexed')).toBeVisible();
    await expect(page.locator('#issue-unindexed-title')).toContainText(/isn't indexed|aren't indexed/);
    await showFiles(page, 'unindexed');
    await expect(page.locator('#issue-unindexed-files td', { hasText: firstPhoto }).first()).toBeVisible();

    await clickFix(page, 'index-new-button', '/library/health/sync');
    await waitForStatZero(page, 'stat-unindexed');
  });

  test('index_photos_index_them_leaves_orphans', async ({ page }) => {
    deleteDroppedPhoto(firstPhoto);
    secondPhoto = dropNewPhoto('b');
    await openLibraryHealthScanned(page);
    await expect(page.locator('#issue-unindexed')).toBeVisible();
    await expect(page.locator('#issue-orphaned')).toBeVisible();
    await expect(page.locator('#status-in-sync')).toBeHidden();
    await showFiles(page, 'orphaned');
    await expect(page.locator('#issue-orphaned-files td', { hasText: 'File deleted from disk' }).first()).toBeVisible();
    await expect(page.locator('#issue-orphaned-files code', { hasText: firstPhoto }).first()).toBeVisible();

    await clickFix(page, 'index-new-button', '/library/health/sync');
    await waitForStatZero(page, 'stat-unindexed');
    expect(await statValue(page, 'stat-orphaned'), 'Index them must leave the orphaned entry').toBeGreaterThan(0);
    await expect(page.locator('#issue-orphaned')).toBeVisible();
  });

  test('index_photos_remove_orphaned_entries', async ({ page }) => {
    await clickFix(page, 'remove-orphaned-button', '/library/health/sync');
    await waitForStatZero(page, 'stat-orphaned');
    await expect(page.locator('#issue-orphaned')).toBeHidden();
  });

  test('index_photos_back_in_sync', async ({ page }) => {
    deleteDroppedPhoto(secondPhoto);
    await clickFix(page, 'remove-orphaned-button', '/library/health/sync');
    await waitForStatZero(page, 'stat-orphaned');
    await expectInSync(page);
  });

  // Regenerating thumbnails: an assigned face whose crop file was deleted is
  // counted, then written again with the face still the person's.
  let faceId = 0;

  test('index_photos_missing_thumbnail_is_counted', async ({ page }) => {
    const thumbnailDir = await readThumbnailDir(page);
    personId = await createPersonViaApi(page, PERSON_NAME);
    faceId = await pickUnassignedFaceId(page);
    await assignFaceViaApi(page, faceId, personId);
    await expect(async () => {
      await page.goto(`/people/${personId}/faces`);
      await expect(page.locator(`[data-face-id="${faceId}"]`)).toBeVisible({ timeout: 1000 });
    }).toPass({ timeout: 10_000 });
    const crop = await page.request.get(`/faces/${faceId}`);
    expect(crop.ok()).toBeTruthy();
    const cropFile = findFileWithContentIn(thumbnailDir, await crop.body());
    expect(cropFile, 'the face crop file in the thumbnail folder').not.toBeNull();
    removeTempFile(cropFile);
    expect((await page.request.get(`/faces/${faceId}`)).status()).toBe(404);

    await openLibraryHealthScanned(page);
    expect(await statValue(page, 'stat-missing-thumbnails')).toBeGreaterThan(0);
    await expect(page.locator('#issue-missing-thumbnails')).toBeVisible();
    await expect(page.locator('#issue-missing-thumbnails-title')).toContainText(/thumbnails? (is|are) missing/);
    await expect(page.locator('#status-in-sync')).toBeHidden();
  });

  test('index_photos_regenerate_missing_thumbnails', async ({ page }) => {
    // Regenerating starts straight away: the 202 arrives with no confirmation step.
    await clickFix(page, 'regenerate-thumbnails-button', '/library/health/regenerate-thumbnails');
    await waitForStatZero(page, 'stat-missing-thumbnails');
    await expect(page.locator('#issue-missing-thumbnails')).toBeHidden();
    // The files land before the job is marked finished; until then it's a job card,
    // then its run history row carries the outcome.
    await expect(async () => {
      await openLibraryHealth(page);
      await expect(page.locator('.index-run-history')).toContainText(/Regenerated \d+ thumbnails?/, { timeout: 1000 });
    }).toPass({ timeout: 10_000, intervals: [1_000] });

    // The crop is back, and the face is still that person's.
    expect((await page.request.get(`/faces/${faceId}`)).status()).toBe(200);
    await page.goto(`/people/${personId}/faces`);
    await expect(page.locator(`[data-face-id="${faceId}"]`)).toBeVisible();
  });

  // The "no media directories configured" empty state is intentionally not
  // exercised: reaching it means removing the seeded library directory, and if the
  // hourly file_sync automation ticks in that window it deletes every media row as
  // "unconfigured" — destroying the shared sandbox for the parallel suites.
});


test.describe('Library Health — responsive', () => {
  test('utilities_nav_sections_are_separate_peer_panels', async ({ page }) => {
    for (const panelId of ['utilities-nav', 'automations-nav']) {
      await expectPanelContract(page, { route: '/library/health', panelId });
    }
    await page.setViewportSize(VIEWPORTS.minimum);
    await page.goto('/library/health');
    await page.locator('#utilities-nav-toggle').click();
    await page.locator('#automations-nav-toggle').click();
    await expect(page.locator('#utilities-nav')).toBeHidden();
    await expect(page.locator('#automations-nav')).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(page.locator('#automations-nav')).toBeHidden();
    await expectNoPageOverflow(page);
  });

  test('index_photos_stats_and_results_fit_a_narrow_viewport', async ({ page }) => {
    const longPath = '/photos/' + 'long-directory-name'.repeat(18) + '/photo.jpg';
    await page.route('**/library/health/scan', route => route.fulfill({
      contentType: 'application/x-ndjson',
      body: JSON.stringify({ type: 'done', total_filesystem: 40, total_imported: 0,
        total_indexed: 0, orphaned: [], empty_roots: [], missing_thumbnails: 0,
        unindexed: Array.from({length: 40}, (_, i) => ({ filename: `photo-${i}.jpg`, full_path: longPath })) }) + '\n',
    }));
    await page.goto('/library/health');
    await expect(page.locator('#issue-unindexed-files tbody tr')).toHaveCount(40);
    await showFiles(page, 'unindexed');
    for (const viewport of Object.values(VIEWPORTS)) {
      await page.setViewportSize(viewport);
      await expectNoPageOverflow(page);
      const scroller = page.locator('#issue-unindexed-files .table-container');
      expect(await scroller.evaluate(el => getComputedStyle(el).overflowY)).toBe('auto');
      expect(await scroller.evaluate(el => el.scrollHeight > el.clientHeight)).toBe(true);
      await scroller.evaluate(el => { el.scrollTop = el.scrollHeight; el.scrollLeft = el.scrollWidth; });
      expect(await scroller.evaluate(el => el.scrollTop)).toBeGreaterThan(0);
      await page.locator('#index-new-button').scrollIntoViewIfNeeded();
      await expectFitsViewport(page, '#index-new-button');
      await expect(page.locator('#stat-unindexed')).toHaveText('40');
    }
  });

  test('utilities_touch_dialog_preserves_input_across_resize', async ({ browser }) => {
    await withTouchContext(browser, VIEWPORTS.minimum, async page => {
      await page.goto('/library/health');
      await page.locator('#automations-nav-toggle').tap();
      await page.locator('#new-automation-button').tap();
      await page.locator('#new-automation-name').fill('Unsaved automation');
      for (const viewport of [VIEWPORTS.tabletPortrait, VIEWPORTS.desktop, VIEWPORTS.minimum]) {
        await page.setViewportSize(viewport);
        await expect(page.locator('#new-automation-name')).toHaveValue('Unsaved automation');
        await expectFitsViewport(page, '#newAutomationModal .modal-content');
      }
      await page.locator('#newAutomationModal .modal-actions [name="cancel"]').tap();
      await expect(page.locator('#newAutomationModal')).not.toHaveClass(/active/);
    });
  });
});
