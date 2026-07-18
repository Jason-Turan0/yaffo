import { test, expect, Page } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

const UNIQ = Date.now();
const SPEC_FILE_STEM = `spec-index-${UNIQ}`;

// The suite drops/removes a real file in the media directory and syncs the database
// around it, so it runs serially and cleans back to the in-sync baseline. The Flask
// server and this test process share a filesystem (local isolated environment), so
// node:fs is the setup mechanism.
test.describe.configure({ mode: 'serial', timeout: 900_000 });

let mediaDir: string;
let copiedFile: string | null = null;
let copiedFilename: string | null = null;

async function readMediaDir(page: Page): Promise<string> {
  await page.goto('/settings');
  const dir = (await page.locator('.media-dir-item .media-dir-path').first().textContent())!.trim();
  expect(dir.length).toBeGreaterThan(0);
  return dir;
}

function findAnyPhoto(dir: string): string {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true, recursive: true })) {
    if (entry.isFile() && /\.(jpe?g|png|heic)$/i.test(entry.name)) {
      return path.join(entry.parentPath ?? (entry as { path?: string }).path ?? dir, entry.name);
    }
  }
  throw new Error(`No supported photo found under ${dir}`);
}

async function openIndexPhotos(page: Page): Promise<void> {
  await page.goto('/utilities/index-photos');
  await expect(page.locator('.page-header')).toContainText('Index Photos');
}

// The scan streams NDJSON; the final record fills every stat. Wait for the last
// counter to leave its '—' placeholder.
async function waitForScanDone(page: Page): Promise<void> {
  for (const stat of ['stat-total-filesystem', 'stat-total-imported', 'stat-total-indexed', 'stat-unindexed', 'stat-orphaned']) {
    await expect(page.locator(`#${stat}`)).not.toHaveText('—', { timeout: 60_000 });
  }
}

async function statValue(page: Page, id: string): Promise<number> {
  return Number((await page.locator(`#${id}`).textContent())!.replace(/[^\d]/g, ''));
}

// Click Sync Database and wait (re-scanning via page reloads) until both work
// counters return to zero. Importing runs face detection + classification in the
// background worker, so the first pass can take a while.
async function syncAndWaitForZero(page: Page): Promise<void> {
  await expect(page.locator('#sync-button')).toBeVisible();
  await expect(page.locator('#sync-button')).toBeEnabled();
  await Promise.all([
    page.waitForResponse(response =>
      response.url().includes('/utilities/index-photos/sync') && response.status() === 202),
    page.locator('#sync-button').click(),
  ]);

  // Importing runs behind the shared taskq worker; parallel suites (e.g. the
  // labels reclassify-all test) can queue minutes of CLIP work ahead of it, so
  // the budget is generous.
  await expect(async () => {
    await openIndexPhotos(page);
    await waitForScanDone(page);
    expect(await statValue(page, 'stat-unindexed')).toBe(0);
    expect(await statValue(page, 'stat-orphaned')).toBe(0);
  }).toPass({ timeout: 360_000, intervals: [3_000] });

  // One more clean load before asserting the settled UI: a load that races the
  // finishing import can reveal #sync-button (the reveal is one-way per document)
  // even though a later scan record on the same page reports everything in sync.
  await openIndexPhotos(page);
  await waitForScanDone(page);
  await expect(page.locator('#scan-results .empty-state')).toContainText('Everything is in sync');
  await expect(page.locator('#sync-button')).toBeHidden();
}

test.describe('Index Photos', () => {
  test.afterAll(() => {
    // If a test failed mid-flight, remove the dropped file so the next full sync
    // returns the sandbox to baseline.
    if (copiedFile && fs.existsSync(copiedFile)) fs.unlinkSync(copiedFile);
  });

  test('index_photos_scan_shows_stats', async ({ page }) => {
    mediaDir = await readMediaDir(page);

    await openIndexPhotos(page);
    await waitForScanDone(page);

    // All five counters are populated with numbers after the scan. (Per-tick live
    // updates of "Total on Filesystem" aren't reliably observable on a small
    // library — the stream finishes in one beat — so the populated end state is
    // the asserted contract.)
    expect(await statValue(page, 'stat-total-filesystem')).toBeGreaterThan(0);
    expect(await statValue(page, 'stat-total-imported')).toBeGreaterThan(0);
    expect(await statValue(page, 'stat-total-indexed')).toBeGreaterThan(0);

    // The results area renders either the in-sync empty state or work tables.
    const results = page.locator('#scan-results');
    const unindexed = await statValue(page, 'stat-unindexed');
    const orphaned = await statValue(page, 'stat-orphaned');
    if (unindexed === 0 && orphaned === 0) {
      await expect(results.locator('.empty-state')).toContainText('Everything is in sync');
    } else {
      await expect(results.locator('.section h2').first()).toBeVisible();
    }
  });

  test('index_photos_sync_database', async ({ page }) => {
    // Drop a new (content-unique) photo into the media directory. Preserve the
    // source extension because the primary Bennett fixture is PNG while the
    // peer fixture is JPEG.
    const source = findAnyPhoto(mediaDir);
    copiedFilename = `${SPEC_FILE_STEM}${path.extname(source).toLowerCase()}`;
    copiedFile = path.join(mediaDir, copiedFilename);
    fs.copyFileSync(source, copiedFile);
    fs.appendFileSync(copiedFile, Buffer.from(`spec-${UNIQ}`));

    // The scan reports the new file as unindexed and shows it in the results table.
    await openIndexPhotos(page);
    await waitForScanDone(page);
    expect(await statValue(page, 'stat-unindexed')).toBeGreaterThan(0);
    const unindexedSection = page.locator('#scan-results .section')
      .filter({ has: page.locator('h2', { hasText: 'Unindexed Photos' }) });
    await expect(unindexedSection).toBeVisible();
    await expect(unindexedSection.locator('td', { hasText: copiedFilename }).first()).toBeVisible();

    // Sync imports it; the button is only revealed when there is work (the helper
    // asserts the in-sync state and hidden button on a settled load).
    await syncAndWaitForZero(page);

    // Now delete the file from disk: the next scan reports the database row as
    // orphaned with its reason, and syncing clears it.
    fs.unlinkSync(copiedFile);
    copiedFile = null;
    await openIndexPhotos(page);
    await waitForScanDone(page);
    expect(await statValue(page, 'stat-orphaned')).toBeGreaterThan(0);
    const orphanedSection = page.locator('#scan-results .section')
      .filter({ has: page.locator('h2', { hasText: 'Orphaned Database Entries' }) });
    await expect(orphanedSection).toBeVisible();
    await expect(orphanedSection.locator('td', { hasText: 'File deleted from disk' }).first()).toBeVisible();
    await expect(orphanedSection.locator('code', { hasText: copiedFilename }).first()).toBeVisible();

    await syncAndWaitForZero(page);
  });

  // The "no media directories configured" empty state is intentionally not
  // exercised: reaching it means removing the seeded library directory, and if the
  // hourly file_sync automation ticks in that window it deletes every media row as
  // "unconfigured" — destroying the shared sandbox for the parallel suites.
});
