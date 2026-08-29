import { test, expect, Page, BrowserContext, Locator } from '@playwright/test';
import { join } from 'path';
import { tmpdir } from 'os';
import { listFilesRecursive, resetTempDir } from '../_support/sandbox-fs';

// Two-instance suite: `page` (Playwright's fixture, BASE_URL) is instance A —
// the seeded library that GRANTS shares; `pageB` (created per test against
// PEER_URL) is instance B — the Obama-library peer that BROWSES and PULLS.
//
// Start the environment with `npm run isolatedEnvironment:start:sharing`
// (A on 5002, B on 5003; both p2p-enabled, hub unreachable, LAN/mDNS only).
//
// THE SCENARIOS ARE STATEFUL AND ORDERED: pairing, grants, and the download
// directory persist server-side, and later tests build on earlier ones
// (e.g. the revoke tests consume the media-dir grant made earlier, and the
// last test re-pairs after the device revoke). mode 'default' pins them to
// one worker in file order — do not parallelize or reorder.
test.describe.configure({ mode: 'default' });

const PEER_URL = process.env.PEER_URL || 'http://127.0.0.1:5003';

// B's download directory, chosen BY THE TESTS so filesystem assertions work:
// the suite runs on the same machine as both instances.
const DOWNLOAD_DIR = join(tmpdir(), 'yaffo_ui_test_downloads');

// The Bennett family's Chicago trip folder on A (keep these values in sync with the
// exported fixture constants in lib/services/isolated_runner.ts).
const SHARED_TRIP_FOLDER = '2015_chicago_baby_trip';
const SHARED_TRIP_PHOTOS = [
  '2015-10-09_103400_chicago-riverwalk.png',
  '2015-10-09_151800_lakefront.png',
  '2015-10-10_110700_neighborhood-walk.png',
  '2015-10-11_085600_family-breakfast.png',
];

// Cached device ids (stable for the sandbox's lifetime).
let idA = '';
let idB = '';

// Recursive file listing via the reviewed _support helper (generated tests may
// not touch fs directly).
const walkFiles = listFilesRecursive;

// The global toast (static/notification.js): one div.notification that gains
// `visible` plus a type class, then hides itself after a few seconds.
async function expectToast(page: Page, pattern: RegExp, timeout = 30_000): Promise<void> {
  await expect(page.locator('.notification.visible')).toContainText(pattern, { timeout });
}

// The app-wide confirm modal (components/confirm-dialog.js). Sharing's
// destructive buttons open it via data-sharing-confirm (the htmx:confirm
// event is intercepted, so no native window.confirm ever appears).
async function acceptConfirmDialog(page: Page): Promise<void> {
  await expect(page.locator('#global-confirm-dialog.active')).toBeVisible();
  await page.locator('#confirm-dialog-confirm').click();
}

// Selects an option in the custom searchable-select widget that wraps a native
// <select> (the native element is hidden, so locator.selectOption won't work).
async function pickSearchableOption(page: Page, selectSelector: string, optionText: string | RegExp): Promise<void> {
  const wrapper = page.locator(`${selectSelector} + .searchable-select-wrapper`);
  await wrapper.locator('.searchable-select-display').click();
  await wrapper.locator('.searchable-select-option').filter({ hasText: optionText }).first().click();
}

async function getDeviceId(page: Page): Promise<string> {
  await page.goto('/sharing/settings');
  const id = (await page.locator('.device-status-strip code.device-id').first().innerText()).trim();
  expect(id).toMatch(/^[A-Z0-9]{4}(-[A-Z0-9]{4}){3}$/);
  return id;
}

async function deviceIds(pageA: Page, pageB: Page): Promise<{ idA: string; idB: string }> {
  if (!idA) idA = await getDeviceId(pageA);
  if (!idB) idB = await getDeviceId(pageB);
  return { idA, idB };
}

// Pair the two instances: generate a code on A, paste it on B. Idempotent —
// pairing again just refreshes the existing trust.
async function pairDevices(pageA: Page, pageB: Page): Promise<{ idA: string; idB: string }> {
  const ids = await deviceIds(pageA, pageB);
  await pageA.goto('/sharing/settings');
  await pageA.getByRole('button', { name: 'Generate pairing code' }).click();
  const codeField = pageA.locator('#pairing-code-box .pairing-code-text');
  await expect(codeField).toBeVisible();
  const code = (await codeField.inputValue()).trim();
  expect(code.length).toBeGreaterThan(20);

  await pageB.goto('/sharing/settings');
  await pageB.locator('.accept-pairing-form textarea[name="code"]').fill(code);
  await pageB.locator('.accept-pairing-form button[type="submit"]').click();
  await expectToast(pageB, /Paired with/);
  return ids;
}

// Sidebar device entry (a link to /sharing/devices/<id>). Presence chips come
// from LAN discovery, so allow a few reloads for the chip to settle.
async function expectSidebarDevice(page: Page, deviceId: string, chipText: RegExp, timeoutMs = 20_000): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  const link = page.locator(`#sharing-sidebar .panel-nav a[href*="${deviceId}"]`, { has: page.locator('.chip', { hasText: chipText }) });
  for (;;) {
    await page.goto('/sharing/settings');
    if (await link.count()) return;
    if (Date.now() > deadline) break;
    await page.waitForTimeout(1_000);
  }
  await expect(link).toHaveCount(1); // fail with a useful message
}

// A row in the sidebar's "Shared With Me" (loaded via htmx and backed by a live
// p2p call to the peer, so allow a few reloads). Returns its View link.
async function sharedWithMeView(page: Page, rowText: string | RegExp, timeoutMs = 20_000): Promise<Locator> {
  const deadline = Date.now() + timeoutMs;
  const row = page.locator('#sharing-sidebar-shared-with-me .sharing-share-nav-item', { hasText: rowText });
  for (;;) {
    await page.goto('/sharing/settings');
    await expect(page.locator('#sharing-sidebar-shared-with-me h3')).toBeVisible();
    if (await row.count()) return row.first().locator('a', { hasText: 'View' });
    if (Date.now() > deadline) break;
    await page.waitForTimeout(1_000);
  }
  await expect(row.first(), `No "Shared With Me" row matching ${rowText}`).toBeVisible();
  return row.first().locator('a', { hasText: 'View' });
}

// The media-dir <select> option text is "<name> - <path>"; both halves are
// useful (the name labels the grant in the sidebar, the path feeds folder grants).
async function mediaDirNameAndPath(pageA: Page, deviceId: string): Promise<{ name: string; path: string }> {
  await pageA.goto(`/sharing/devices/${deviceId}`);
  const optionText = (await pageA.locator('select#share-media-dir option').first().innerText()).trim();
  const separator = optionText.indexOf(' - ');
  expect(separator).toBeGreaterThan(0);
  return { name: optionText.slice(0, separator), path: optionText.slice(separator + 3) };
}

// Grant B the (single) seeded media directory from A's device panel.
async function grantMediaDir(pageA: Page, deviceId: string): Promise<string> {
  const { name } = await mediaDirNameAndPath(pageA, deviceId);
  await pageA.locator('.share-grant-form button[type="submit"]').click();
  await expectToast(pageA, /Share grant added/);
  return name;
}

// Revoke every "Shared With Others" row matching `rowText` from A's sidebar.
async function revokeOutboundShares(pageA: Page, rowText: string | RegExp): Promise<void> {
  await pageA.goto('/sharing/settings');
  const rows = pageA.locator('.sharing-shares-nav .sharing-share-nav-item', { hasText: rowText });
  while (await rows.count()) {
    await rows.first().locator('button', { hasText: 'Revoke' }).click();
    await acceptConfirmDialog(pageA);
    await expectToast(pageA, /Share grant revoked/);
    await pageA.goto('/sharing/settings');
  }
}

// A completed transfer batch that moved exactly `filesTotal` files. The panel
// self-polls every 2s while a batch is active, so this just waits it out.
async function expectBatchCompleted(page: Page, filesTotal: number, timeout = 60_000): Promise<void> {
  const meta = new RegExp(`\\b${filesTotal} of ${filesTotal} files?\\b`);
  const batch = page.locator('.transfer-batch[data-state="completed"]', { hasText: meta });
  await expect(batch.first()).toBeVisible({ timeout });
}

test.describe('Sharing Feature', () => {
  let contextB: BrowserContext;
  let pageB: Page;

  test.beforeEach(async ({ browser }) => {
    test.setTimeout(90_000); // pairing and pulls involve live p2p calls
    contextB = await browser.newContext({ baseURL: PEER_URL });
    pageB = await contextB.newPage();
  });

  test.afterEach(async () => {
    await contextB?.close();
  });

  test('sharing_settings_shows_this_device - The sharing settings page shows this device identity and hub state', async ({ page }) => {
    await page.goto('/sharing/settings');

    // Status strip: device id with a Copy action
    const strip = page.locator('.device-status-strip');
    await expect(strip).toBeVisible();
    await expect(strip.locator('code.device-id').first()).toHaveText(/^[A-Z0-9]{4}(-[A-Z0-9]{4}){3}$/);
    await expect(strip.locator('button[data-copy-text]')).toHaveText('Copy');

    // No hub is reachable in this environment
    await expect(strip.locator('.device-status-item', { hasText: 'Sharing hub' }).locator('.chip')).toHaveText('Disconnected');

    // Both pairing forms are offered
    await expect(page.getByRole('heading', { name: 'Show a pairing code' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Generate pairing code' })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Enter a pairing code' })).toBeVisible();
    await expect(page.locator('.accept-pairing-form textarea[name="code"]')).toBeVisible();
  });

  test('sharing_pair_two_devices_over_the_lan - Two instances pair with no hub and see each other as local devices', async ({ page }) => {
    const ids = await deviceIds(page, pageB);

    // Generate the code on A: shown as text and QR with an expiry countdown
    await page.goto('/sharing/settings');
    await page.getByRole('button', { name: 'Generate pairing code' }).click();
    const codeField = page.locator('#pairing-code-box .pairing-code-text');
    await expect(codeField).toBeVisible();
    await expect(page.locator('#pairing-code-box img.pairing-code-qr')).toBeVisible();
    await expect(page.locator('#pairing-code-box .pairing-code-countdown')).toHaveText(/\d+:\d{2}/);
    const code = (await codeField.inputValue()).trim();
    expect(code.length).toBeGreaterThan(20);

    // Paste it on B: pairing succeeds and the confirmation names A
    await pageB.goto('/sharing/settings');
    await pageB.locator('.accept-pairing-form textarea[name="code"]').fill(code);
    await pageB.locator('.accept-pairing-form button[type="submit"]').click();
    await expectToast(pageB, new RegExp(`Paired with .*${ids.idA}`));

    // Each side lists the other with a "Local" chip (found over the LAN, not the hub)
    await expectSidebarDevice(pageB, ids.idA, /Local/);
    await expectSidebarDevice(page, ids.idB, /Local/);

    // Opening the paired device shows its id and pairing time
    await pageB.goto(`/sharing/devices/${ids.idA}`);
    await expect(pageB.locator('#device-panel code.device-id')).toHaveText(ids.idA);
    await expect(pageB.locator('.device-identity-item', { hasText: 'Paired:' })).toBeVisible();
  });

  test('sharing_grant_a_media_directory_and_browse_it - A shares a media directory and B browses it with filters', async ({ page }) => {
    const ids = await deviceIds(page, pageB);
    const mediaDirName = await grantMediaDir(page, ids.idB);

    // On A the grant is listed under "Shared With Others", named by the media dir
    await page.goto('/sharing/settings');
    await expect(page.locator('.sharing-shares-nav .sharing-share-nav-item', { hasText: mediaDirName })).toHaveCount(1);

    // On B the share appears under "Shared With Me" and opens A's gallery
    const viewLink = await sharedWithMeView(pageB, mediaDirName);
    await viewLink.click();
    await expect(pageB.locator('.remote-photo-grid')).toBeVisible();
    const totalText = await pageB.locator('.page-header .subtitle').innerText();
    const total = Number(totalText.match(/of ([\d,]+) shared/)?.[1]?.replace(/,/g, ''));
    expect(total).toBeGreaterThan(0);

    // Previews are live p2p calls back to A. Observe the browser's bounded queue and
    // retry rather than adding an unqueued request that competes with the UI loads.
    const preview = pageB.locator('.remote-photo-card img').first();
    await expect.poll(() => preview.evaluate((image: HTMLImageElement) => {
      const expectedSrc = new URL(image.dataset.previewSrc!, document.baseURI).href;
      return image.dataset.previewState === 'done'
        && image.currentSrc === expectedSrc
        && image.complete
        && image.naturalWidth > 0;
    }), { timeout: 30_000 }).toBe(true);

    // The filter sidebar is built from A's facets; filtering narrows the results
    const yearSelect = pageB.locator('select#year-select');
    await expect(yearSelect).toBeAttached();
    const yearToSelect = await yearSelect.locator('option').nth(1).getAttribute('value');
    expect(yearToSelect).not.toBeNull();
    await pickSearchableOption(pageB, 'select#year-select', yearToSelect!);
    await pageB.getByRole('button', { name: 'Apply Filters' }).click();
    await pageB.waitForURL(new RegExp(`[?&]year=${yearToSelect}`));
    await expect(pageB.locator('.remote-photo-card').first()).toBeVisible();
    const filteredText = await pageB.locator('.page-header .subtitle').innerText();
    const filteredTotal = Number(filteredText.match(/of ([\d,]+) shared/)?.[1]?.replace(/,/g, ''));
    expect(filteredTotal).toBeGreaterThan(0);
    expect(filteredTotal).toBeLessThan(total);
  });

  test('sharing_gallery_without_a_download_directory - Without a download directory nothing is selectable, then set one', async ({ page }) => {
    const ids = await deviceIds(page, pageB);

    // B may start with or without a download directory depending on whether the
    // environment is pristine or re-used. The gallery either shows a notice (no
    // download dir) or offers selection (download dir already set).
    const viewLink = await sharedWithMeView(pageB, /.+/);
    await viewLink.click();
    await expect(pageB.locator('.remote-photo-grid')).toBeVisible();

    const notice = pageB.locator('.remote-notice');
    if (await notice.count() > 0) {
      // No download directory: the gallery shows a notice instead of selection
      await expect(notice).toContainText('Choose a download directory');
      await expect(notice.locator('a', { hasText: 'Set download directory' })).toBeVisible();

      // No selection is offered: no selection bar, no pull button, grid not selecting
      await expect(pageB.locator('#remote-selection')).toHaveCount(0);
      await expect(pageB.locator('#pull-selected-button')).toHaveCount(0);
      await expect(pageB.locator('.remote-photo-grid')).not.toHaveClass(/is-selecting/);

      // Restore/set B's download directory (spec cleanup) — to a path this suite
      // controls, so later tests can assert on the pulled files
      resetTempDir(DOWNLOAD_DIR);
      await notice.locator('a', { hasText: 'Set download directory' }).click();
      await pageB.locator('#shared-download-dir').fill(DOWNLOAD_DIR);
      await pageB.locator('.share-download-dir-form button[type="submit"]').click();
      await expectToast(pageB, /Download directory saved/);
    } else {
      // Download directory already set — selection is offered, grid is selectable
      await expect(pageB.locator('#remote-selection')).toBeVisible();
      await expect(pageB.locator('.remote-photo-grid')).toHaveClass(/is-selecting/);
      // Ensure the download directory is the path this suite controls
      resetTempDir(DOWNLOAD_DIR);
      await pageB.goto('/sharing/settings');
      await pageB.locator('#shared-download-dir').fill(DOWNLOAD_DIR);
      await pageB.locator('.share-download-dir-form button[type="submit"]').click();
      await expectToast(pageB, /Download directory saved/);
    }

    // With a directory set the gallery offers selection again
    await pageB.goto(`/sharing/devices/${ids.idA}`);
    const view = await sharedWithMeView(pageB, /.+/);
    await view.click();
    await expect(pageB.locator('#remote-selection')).toBeVisible();
    await expect(pageB.locator('.remote-photo-grid')).toHaveClass(/is-selecting/);
  });

  test('sharing_pull_selected_photos - B selects two photos and pulls them as a background batch', async () => {
    const viewLink = await sharedWithMeView(pageB, /.+/);
    await viewLink.click();
    await expect(pageB.locator('.remote-photo-grid.is-selecting')).toBeVisible();

    // Tick two photos: live count, selection carried in the URL
    const cards = pageB.locator('.remote-photo-card');
    await cards.nth(0).click();
    await cards.nth(1).click();
    await expect(pageB.locator('#remote-selection [data-selection-count]')).toHaveText(/\b2 selected/);
    expect(pageB.url()).toMatch(/select_id=\d+&.*select_id=\d+|select_id=\d+.*&select_id=\d+/);

    // Pull: a background batch is queued and confirmed with the file count
    await pageB.locator('#pull-selected-button').click();
    await expectToast(pageB, /Pull of 2 files queued/);

    // The transfers panel shows the batch through to completion
    await expectBatchCompleted(pageB, 2);
  });

  test('sharing_pull_everything_matching - B pulls every photo matching a filter minus one unticked', async () => {
    const viewLink = await sharedWithMeView(pageB, /.+/);
    await viewLink.click();
    await expect(pageB.locator('.remote-photo-grid.is-selecting')).toBeVisible();

    // Filter first, so "all" means "all MATCHING". Probe the year facets for a
    // year matching MORE than one file (unticking one must leave something to pull).
    const unfiltered = Number((await pageB.locator('.page-header .subtitle').innerText()).match(/of ([\d,]+) shared/)?.[1]?.replace(/,/g, ''));
    const yearValues: string[] = [];
    for (const option of await pageB.locator('select#year-select option').all()) {
      const value = await option.getAttribute('value');
      if (value) yearValues.push(value);
    }
    let headerTotal = 0;
    let chosenYear = '';
    for (const year of yearValues) {
      await pickSearchableOption(pageB, 'select#year-select', year);
      await pageB.getByRole('button', { name: 'Apply Filters' }).click();
      await pageB.waitForURL(new RegExp(`[?&]year=${year}`));
      await expect(pageB.locator('.remote-photo-card').first()).toBeVisible();
      headerTotal = Number((await pageB.locator('.page-header .subtitle').innerText()).match(/of ([\d,]+) shared/)?.[1]?.replace(/,/g, ''));
      if (headerTotal > 1 && headerTotal < unfiltered) {
        chosenYear = year;
        break;
      }
    }
    expect(chosenYear, `No year facet with 2..${unfiltered - 1} matches among ${yearValues.join(', ')}`).not.toBe('');

    // The chip names the number MATCHING THE FILTER (not the page). Retry the
    // read: selection_bar.js re-renders the label at app init.
    const toggle = pageB.locator('#remote-selection [data-selection-toggle]');
    await expect(toggle).toHaveText(/Select all [\d,]+ matching/, { timeout: 10_000 });
    // textContent, not innerText: chips are text-transform: uppercase, and
    // innerText returns the RENDERED text ("SELECT ALL 4 MATCHING").
    const chipText = ((await toggle.textContent()) || '').replace(/\s+/g, ' ');
    const matching = Number(chipText.match(/Select all ([\d,]+) matching/)?.[1]?.replace(/,/g, ''));
    expect(matching).toBeGreaterThan(1);
    expect(matching).toBe(headerTotal);

    // Select all, then untick one: "everything, except this one"
    await toggle.click();
    await expect(pageB.locator('#remote-selection [data-selection-count]')).toHaveText(new RegExp(`\\b${matching} selected`));
    await pageB.locator('.remote-photo-card').first().click();
    await expect(pageB.locator('#remote-selection [data-selection-count]')).toHaveText(new RegExp(`\\b${matching - 1} selected`));
    expect(pageB.url()).toMatch(/select=all/);
    expect(pageB.url()).toMatch(/exclude_id=\d+/);

    // Pull: the batch resolves the manifest on the peer, including unrendered pages
    await pageB.locator('#pull-selected-button').click();
    await expectToast(pageB, /Download queued — everything selected/);
    await expectBatchCompleted(pageB, matching - 1);
  });

  test('sharing_grant_a_folder - A shares one subfolder and B sees only its photos', async ({ page }) => {
    const ids = await deviceIds(page, pageB);

    // Share the Chicago trip subfolder of the media directory
    const { path: mediaDirPath } = await mediaDirNameAndPath(page, ids.idB);
    await pickSearchableOption(page, 'select#share-scope-type', 'Folder');
    const folderInput = page.locator('#share-folder-path');
    await expect(folderInput).toBeEnabled();
    await folderInput.fill(`${mediaDirPath}/${SHARED_TRIP_FOLDER}`);
    await page.locator('.share-grant-form button[type="submit"]').click();
    await expectToast(page, /Share grant added/);

    // On A it is listed under "Shared With Others", named by the folder path
    await page.goto('/sharing/settings');
    await expect(page.locator('.sharing-shares-nav .sharing-share-nav-item', { hasText: SHARED_TRIP_FOLDER })).toHaveCount(1);

    // On B the share opens a gallery of the folder's photos and NOTHING else
    const viewLink = await sharedWithMeView(pageB, SHARED_TRIP_FOLDER);
    await viewLink.click();
    await expect(pageB.locator('.remote-photo-card')).toHaveCount(SHARED_TRIP_PHOTOS.length);
    for (const name of SHARED_TRIP_PHOTOS) {
      await expect(pageB.locator(`.remote-photo-card img[alt="${name}"]`)).toBeAttached();
    }

    // Cleanup: revoke the folder share
    await revokeOutboundShares(page, SHARED_TRIP_FOLDER);
  });

  test('sharing_grant_an_album - A shares an album and its membership is resolved per request', async ({ page }) => {
    const ids = await deviceIds(page, pageB);

    // Share the seeded album from the album's own Share action
    await page.goto('/albums');
    await page.locator('a', { hasText: 'Seeded Album' }).first().click();
    await page.waitForURL(/\/albums\/\d+/);
    const albumUrl = new URL(page.url());
    await page.locator('#share-album-button').click();
    const modal = page.locator('#shareAlbumModal');
    await expect(modal).toBeVisible();
    await modal.locator(`input[name="device_id"][value="${ids.idB}"]`).check();
    await modal.locator('button[type="submit"]').click();
    await page.waitForLoadState('domcontentloaded');

    // The album shows it is shared, and A's sidebar names the ALBUM
    await page.goto('/albums');
    await expect(page.locator('.chip', { hasText: 'Shared' }).first()).toBeVisible();
    await page.goto('/sharing/settings');
    const albumRow = page.locator('.sharing-shares-nav .sharing-share-nav-item', { hasText: 'Seeded Album' });
    await expect(albumRow).toHaveCount(1);

    // B lists the album's photos. The album may be empty if a prior run removed
    // all members — the share itself still works and can be revoked.
    const viewLink = await sharedWithMeView(pageB, /Seeded Album/);
    await viewLink.click();
    const initialCount = await pageB.locator('.remote-photo-card').count();
    const albumFilesUrl = pageB.url();

    if (initialCount > 0) {
      // A removes one photo from the album (selection rides the URL in edit mode)
      await page.goto(albumUrl.pathname);
      const memberId = await page.locator('#album-grid .photo-card[data-select-id]').first().getAttribute('data-select-id');
      await page.goto(`${albumUrl.pathname}?edit=1&select_id=${memberId}`);
      await page.locator('#remove-from-album-button').click();
      await acceptConfirmDialog(page);
      await page.waitForLoadState('domcontentloaded');
      await expect(page.locator('#album-grid .photo-card')).toHaveCount(initialCount - 1);

      // B's NEXT listing no longer includes it — membership is per request
      await pageB.goto(albumFilesUrl);
      await expect(pageB.locator('.remote-photo-card')).toHaveCount(initialCount - 1);
    }

    // Cleanup: revoke the album share
    await revokeOutboundShares(page, /Seeded Album/);
  });

  test('sharing_album_share_modal_toggle - The album Share modal shares with a device and un-shares it again', async ({ page }) => {
    const ids = await deviceIds(page, pageB);

    await page.goto('/albums');
    await page.locator('a', { hasText: 'Seeded Album' }).first().click();
    await page.waitForURL(/\/albums\/\d+/);
    const albumPath = new URL(page.url()).pathname;
    const albumUrlPattern = new RegExp(`${albumPath.replace(/\//g, '\\/')}$`);

    // The modal should start unchecked (the previous test revoked its album grant).
    // If a prior run left the album shared, clean up first so the toggle is meaningful.
    await page.locator('#share-album-button').click();
    const modal = page.locator('#shareAlbumModal');
    await expect(modal).toBeVisible();
    await expect(modal.locator('input[name="device_id"]')).toHaveCount(1);
    const deviceCheckbox = modal.locator(`input[name="device_id"][value="${ids.idB}"]`);
    const deviceName = (await deviceCheckbox.locator('..').textContent())!.trim();
    expect(deviceName).not.toBe('');

    // If a prior run left a grant in place, revoke it first so we start clean
    if (await deviceCheckbox.isChecked()) {
      await deviceCheckbox.uncheck();
      await modal.locator('button[type="submit"]').click();
      await page.waitForURL(albumUrlPattern);
      // Reopen the modal for the real toggle test
      await page.locator('#share-album-button').click();
      await expect(modal).toBeVisible();
    }
    await expect(deviceCheckbox).not.toBeChecked();

    // Check the paired device and confirm
    await deviceCheckbox.check();
    await modal.locator('button[type="submit"]').click();
    await page.waitForURL(albumUrlPattern);

    // Header names the device; sidebar and overview show a "Shared" chip
    await expect(page.locator('.page-header .subtitle .chip-accent')).toHaveText(deviceName);
    await expect(page.locator('.albums-sidebar .chip', { hasText: 'Shared' })).toHaveCount(1);
    await page.goto('/albums');
    await expect(page.locator('.album-tile .chip', { hasText: 'Shared' })).toHaveCount(1);

    // And it is listed under "Shared With Others", named by the album
    await page.goto('/sharing/settings');
    await expect(page.locator('.sharing-shares-nav .sharing-share-nav-item', { hasText: 'Seeded Album' })).toHaveCount(1);

    // Reopen: the checkbox reflects the truth; unchecking revokes
    await page.goto(albumPath);
    await page.locator('#share-album-button').click();
    await expect(modal).toBeVisible();
    await expect(deviceCheckbox).toBeChecked();
    await deviceCheckbox.uncheck();
    await modal.locator('button[type="submit"]').click();
    await page.waitForURL(albumUrlPattern);

    await expect(page.locator('.page-header .subtitle .chip-accent')).toHaveCount(0);
    await expect(page.locator('.albums-sidebar .chip', { hasText: 'Shared' })).toHaveCount(0);
    await page.goto('/albums');
    await expect(page.locator('.album-tile .chip', { hasText: 'Shared' })).toHaveCount(0);
    await page.goto('/sharing/settings');
    await expect(page.locator('.sharing-shares-nav .sharing-share-nav-item', { hasText: 'Seeded Album' })).toHaveCount(0);
  });

  test('sharing_revoke_a_grant - Revoking a share stops the peer next request but keeps pulled files', async ({ page }) => {
    // Find B's view of a shared gallery first (at least the media-dir grant from
    // the earlier test is still in place; there may also be stale shares from
    // a prior run — revoke them all)
    const viewLink = await sharedWithMeView(pageB, /.+/);
    await viewLink.click();
    await expect(pageB.locator('.remote-photo-grid')).toBeVisible();
    const shareUrl = pageB.url();

    // Revoke every outbound share on A, from the sidebar's "Shared With Others"
    await page.goto('/sharing/settings');
    const rows = page.locator('.sharing-shares-nav .sharing-share-nav-item');
    while (await rows.count() > 0) {
      await rows.first().locator('button', { hasText: 'Revoke' }).click();
      await acceptConfirmDialog(page);
      await expectToast(page, /Share grant revoked/);
      await page.goto('/sharing/settings');
    }
    await expect(page.locator('.sharing-shares-nav .sharing-share-nav-item')).toHaveCount(0);

    // B's next attempt is refused: the share is gone from "Shared With Me" …
    await pageB.goto('/sharing/settings');
    await expect(pageB.locator('#sharing-sidebar-shared-with-me h3')).toBeVisible();
    await expect(pageB.locator('#sharing-sidebar-shared-with-me .sharing-share-nav-item')).toHaveCount(0, { timeout: 15_000 });

    // … and opening the old URL shows no files
    await pageB.goto(shareUrl);
    await expect(pageB.locator('.remote-photo-card')).toHaveCount(0);
    await expect(pageB.locator('.empty-state')).toBeVisible();

    // Files B already pulled are still on B — revocation cannot claw back
    expect(walkFiles(DOWNLOAD_DIR).length).toBeGreaterThan(0);
  });

  test('sharing_revoke_a_device - Revoking a device cuts it off and it can then be deleted', async ({ page }) => {
    const ids = await deviceIds(page, pageB);

    // Revoke B on A's device page; the confirmation warns copied files stay
    await page.goto(`/sharing/devices/${ids.idB}`);
    await page.locator('.page-header button', { hasText: 'Revoke' }).click();
    const dialog = page.locator('#global-confirm-dialog.active');
    await expect(dialog).toContainText(/Files it already copied stay on it/);
    await page.locator('#confirm-dialog-confirm').click();

    // The revoked device shows a "Revoked" chip and offers no sharing
    await expect(page.locator('.page-header .chip', { hasText: 'Revoked' })).toBeVisible();
    await expect(page.locator('#device-panel')).toContainText('This device is revoked');
    await expect(page.locator('.share-grant-form')).toHaveCount(0);

    // B is cut off: its "Shared With Me" from A yields nothing anymore
    await pageB.goto('/sharing/settings');
    await expect(pageB.locator('#sharing-sidebar-shared-with-me h3')).toBeVisible();
    await expect(pageB.locator('#sharing-sidebar-shared-with-me .sharing-share-nav-item')).toHaveCount(0, { timeout: 15_000 });

    // A revoked device can be deleted, and leaves the sidebar
    await page.locator('.page-header button', { hasText: 'Delete' }).click();
    await acceptConfirmDialog(page);
    // The delete endpoint responds with HX-Redirect — wait for it instead of racing it
    await page.waitForURL('**/sharing/settings');
    await expect(page.locator(`#sharing-sidebar .panel-nav a[href*="${ids.idB}"]`)).toHaveCount(0);
  });

  test('sharing_duplicate_grant_is_not_created_twice - Granting the same scope twice yields one share', async ({ page }) => {
    // Re-pair after the device revoke/delete above
    const ids = await pairDevices(page, pageB);

    // Grant the same media directory twice
    const mediaDirName = await grantMediaDir(page, ids.idB);
    await page.goto(`/sharing/devices/${ids.idB}`);
    await page.locator('.share-grant-form button[type="submit"]').click();
    await expect(page.locator('.notification.visible')).toBeVisible();

    // "Shared With Others" lists the share once, not twice
    await page.goto('/sharing/settings');
    await expect(page.locator('.sharing-shares-nav .sharing-share-nav-item', { hasText: mediaDirName })).toHaveCount(1);

    // Cleanup: revoke it
    await revokeOutboundShares(page, mediaDirName);
  });
});
