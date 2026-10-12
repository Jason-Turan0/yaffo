import { expect, test, type Page, type TestInfo } from '@playwright/test';
import { readdirSync } from 'node:fs';
import { join } from 'node:path';

const createdSlugs: string[] = [];
const RUN_TIMEOUT = 25_000;

test.describe.configure({ mode: 'serial' });

async function createSync(page: Page, kind: 'import' | 'send', service: string, name: string): Promise<string> {
  await page.goto('/utilities/automations');
  await page.locator('#new-automation-button').click();
  const modal = page.locator('#newAutomationModal');
  await expect(modal).toHaveClass(/active/);
  await modal.locator(`input[name="kind"][value="${kind}"]`).check();
  const serviceChoices = modal.locator(`[data-sync-services="${kind}"]`);
  const serviceNames = await serviceChoices.locator('.choice-card-title').allTextContents();
  const serviceIndex = serviceNames.findIndex(name => name.trim() === service);
  expect(serviceIndex, `Service "${service}" is missing from ${kind}: ${serviceNames.join(', ')}`).toBeGreaterThanOrEqual(0);
  await serviceChoices.locator('label.choice-card').nth(serviceIndex).locator('input').check();
  await modal.locator('#new-automation-name').fill(name);
  await modal.locator('button[type="submit"]').click();
  await expect(page).toHaveURL(/\/utilities\/automations\/[^/]+$/);
  await expect(page.locator('.page-header h1')).toContainText(name);
  const slug = new URL(page.url()).pathname.split('/').pop()!;
  createdSlugs.push(slug);
  return slug;
}

async function chooseSearchableOption(page: Page, selector: string, text: string | RegExp): Promise<void> {
  const wrapper = page.locator(`${selector} + .searchable-select-wrapper`);
  await wrapper.locator('.searchable-select-display').click();
  await wrapper.locator('.searchable-select-option').filter({ hasText: text }).first().click();
}

async function chooseFolder(page: Page, folderInput: string, path: string): Promise<void> {
  if (!(await page.locator(folderInput).isVisible())) {
    await page.locator('#sync-step-options > summary').click();
  }
  await page.locator(folderInput).fill(path);
  await page.locator(`[data-folder-picker="${folderInput.slice(1)}"]`).click();
  const picker = page.locator('#folder-picker-modal');
  await expect(picker).toHaveClass(/active/);
  await expect(page.locator('#folder-picker-path')).toHaveText(path);
  await page.locator('#folder-picker-select').click();
  await expect(picker).not.toHaveClass(/active/);
  await expect(page.locator(folderInput)).toHaveValue(path);
}

async function saveFolderOptions(page: Page, slug: string): Promise<void> {
  await Promise.all([
    page.waitForResponse(response => response.url().includes(`/utilities/automations/${slug}/sync/options`)
      && response.request().method() === 'POST'),
    page.locator('#sync-step-options button[type="submit"]').click(),
  ]);
  await page.waitForLoadState('domcontentloaded');
}

async function chooseAlbum(page: Page): Promise<number> {
  const syncUrl = page.url();
  await page.goto('/albums');
  const albumPath = await page.locator('.album-tile', { hasText: 'Seeded Album' }).first().getAttribute('href');
  expect(albumPath).not.toBeNull();
  await page.goto(albumPath!);
  const count = await page.locator('#album-grid .photo-card').count();
  expect(count).toBeGreaterThan(0);
  await page.goto(syncUrl);
  await page.locator('#sync-selection-album').check();
  await chooseSearchableOption(page, '#sync-album', 'Seeded Album');
  await Promise.all([
    page.waitForResponse(response => response.url().includes('/sync/selection')
      && response.request().method() === 'POST'),
    page.locator('#sync-step-where [data-selection-submit]').click(),
  ]);
  await page.waitForLoadState('domcontentloaded');
  return count;
}

async function enableSync(page: Page, slug: string): Promise<void> {
  const toggle = page.locator('.automation-actions [role="switch"]');
  await Promise.all([
    page.waitForResponse(response => response.url().includes(`/utilities/automations/${slug}/sync/turn-on`)
      && response.request().method() === 'POST'),
    toggle.click(),
  ]);
  await page.waitForLoadState('domcontentloaded');
  await expect(page.locator('.automation-actions [role="switch"]')).toHaveAttribute('aria-checked', 'true');
}

async function openAccount(page: Page): Promise<void> {
  const account = page.locator('#sync-step-account');
  if (await account.getAttribute('open') === null) {
    await account.locator('summary.sync-step-head').click();
  }
}

async function signInToOneDrive(page: Page, slug: string, expectedScope?: string): Promise<void> {
  await page.locator('#sync-sign-in').click();
  await expect(page.locator('#accept')).toBeVisible();
  if (expectedScope) await expect(page.locator('body')).toContainText(expectedScope);
  await page.locator('#accept').click();
  await expect(page).toHaveURL(new RegExp(`/utilities/automations/${slug}$`));
}

async function fakeOneDriveState(page: Page): Promise<{counters: Record<string, number>}> {
  const fakeUrl = process.env.FAKE_ONEDRIVE_URL;
  expect(fakeUrl, 'test:spec starts scripts/fake_onedrive.py for sync specs').toBeTruthy();
  const response = await page.request.get(`${fakeUrl}/_admin/state`);
  expect(response.ok()).toBeTruthy();
  return response.json();
}

async function deleteSync(page: Page, slug: string): Promise<void> {
  await page.goto(`/utilities/automations/${slug}`);
  await page.locator('#delete-automation-button').click();
  const dialog = page.locator('#global-confirm-dialog.active');
  await expect(dialog).toBeVisible();
  await page.locator('#confirm-dialog-confirm').click();
  await expect(page.locator('.page-header')).not.toContainText(slug);
}

async function createDestination(page: Page, testInfo: TestInfo, slug: string): Promise<string> {
  const parent = testInfo.outputPath();
  const folderName = `sync-destination-${slug}`;
  const input = '#sync-option-folder';
  await chooseFolder(page, input, parent);
  await page.locator('[data-folder-picker="sync-option-folder"]').click();
  const picker = page.locator('#folder-picker-modal');
  await expect(picker).toHaveClass(/active/);
  await page.locator('#folder-picker-create-name').fill(folderName);
  await page.locator('#folder-picker-create-submit').click();
  const destination = join(parent, folderName);
  await expect(page.locator('#folder-picker-path')).toHaveText(destination);
  await page.locator('#folder-picker-select').click();
  await expect(page.locator(input)).toHaveValue(destination);
  return destination;
}

test.afterAll(async ({ browser }) => {
  const baseURL = process.env.BASE_URL || 'http://127.0.0.1:5001';
  const context = await browser.newContext({ baseURL });
  for (const slug of createdSlugs.reverse()) {
    await context.request.post(`/utilities/automations/${slug}/delete`).catch(() => {});
  }
  await context.close();
});

test('new_automation_lists_services_by_direction', async ({ page }) => {
  await page.goto('/utilities/automations');
  await page.locator('#new-automation-button').click();
  const modal = page.locator('#newAutomationModal');
  await expect(modal).toHaveClass(/active/);

  await modal.locator('input[name="kind"][value="import"]').check();
  const imports = modal.locator('[data-sync-services="import"]');
  const importServices = (await imports.locator('.choice-card-title').allTextContents()).map(name => name.trim());
  expect(importServices).toEqual(expect.arrayContaining(['Folder', 'OneDrive', 'Immich']));
  expect(importServices).not.toContain('Paired device');

  await modal.locator('input[name="kind"][value="send"]').check();
  const sends = modal.locator('[data-sync-services="send"]');
  const sendServices = (await sends.locator('.choice-card-title').allTextContents()).map(name => name.trim());
  expect(sendServices).toEqual(expect.arrayContaining(['Folder', 'Google Photos', 'OneDrive', 'Immich']));
  await modal.locator('input[name="kind"][value="custom"]').check();
  await expect(imports).toBeHidden();
  await expect(sends).toBeHidden();
  await modal.locator('.modal-close').click();
});

test('immich_validates_server_and_loads_fake_server_collections', async ({ page }) => {
  const slug = await createSync(page, 'import', 'Immich', `UI Immich Setup ${Date.now()}`);
  try {
    const account = page.locator('#sync-step-account');
    await expect(account).toBeVisible();
    await expect(account.locator('#sync-base-url')).toBeVisible();
    await expect(account.locator('#sync-api-key')).toBeVisible();
    await account.locator('#sync-base-url').fill('http://127.0.0.1:1');
    await account.locator('[data-allow-http] input').check();
    await account.locator('#sync-api-key').fill('ui-test-invalid-key');

    await Promise.all([
      page.waitForResponse(response => response.url().includes(`/utilities/automations/${slug}/sync/credentials`)
        && response.request().method() === 'POST'),
      account.locator('#sync-connect').click(),
    ]);

    await expect(page.locator('.alert-error[role="alert"]')).toContainText("couldn't reach the server");
    await expect(page.locator('#sync-step-options')).toContainText('Sign in first');
    await expect(page.locator('#sync-step-options form')).toHaveCount(0);
    await expect(page.locator('.automation-actions [role="switch"]')).toBeDisabled();

    const fakeImmichUrl = process.env.FAKE_IMMICH_URL;
    expect(fakeImmichUrl, 'test:spec starts scripts/fake_immich.py for sync specs').toBeTruthy();
    await account.locator('#sync-base-url').fill(fakeImmichUrl!);
    await account.locator('[data-allow-http] input').check();
    await account.locator('#sync-api-key').fill('immich-test-key');
    await Promise.all([
      page.waitForResponse(response => response.url().includes(`/utilities/automations/${slug}/sync/credentials`)
        && response.request().method() === 'POST'),
      account.locator('#sync-connect').click(),
    ]);

    await expect(page.locator('#sync-step-account')).toContainText('Signed in');
    await expect(page.locator('#sync-step-options form')).toBeVisible();
    const album = page.locator('[data-remote-option="album"]');
    await album.locator('[data-remote-open]').first().click();
    await expect(album.locator('[data-remote-list]')).toContainText('Family');
  } finally {
    await deleteSync(page, slug);
  }
});

test('onedrive_shares_account_and_reconsents_when_target_needs_write_scope', async ({ page }) => {
  const sourceSlug = await createSync(page, 'import', 'OneDrive', `UI OneDrive Import ${Date.now()}`);
  let targetSlug: string | undefined;
  try {
    await signInToOneDrive(page, sourceSlug);
    await openAccount(page);
    await expect(page.locator('#sync-step-account')).toContainText('Signed in');

    const sourceFolder = page.locator('[data-remote-option="folder"]');
    await sourceFolder.locator('[data-remote-open]').first().click();
    const cameraRoll = sourceFolder.locator('[data-remote-list] .sync-picker-item').filter({ hasText: 'Camera Roll' });
    await expect(cameraRoll).toBeVisible();
    const beforePreview = await fakeOneDriveState(page);
    await Promise.all([
      page.waitForResponse(response => response.url().includes(`/utilities/automations/${sourceSlug}/sync/options`)
        && response.request().method() === 'POST'),
      cameraRoll.getByRole('button', { name: /Choose/ }).click(),
    ]);
    await expect(page.locator('#sync-option-folder')).toHaveValue(/Camera Roll/);
    await page.locator('#sync-test').click();
    await expect(page.locator('#sync-test-card')).toBeVisible();
    await expect(page.locator('#sync-test-meta')).not.toBeEmpty();
    await expect(page.locator('#sync-test-result .sync-test-table tbody tr').first()).toBeVisible();
    const afterPreview = await fakeOneDriveState(page);
    expect(afterPreview.counters.delta_pages ?? 0).toBeGreaterThan(beforePreview.counters.delta_pages ?? 0);
    expect(afterPreview.counters.downloads ?? 0).toBe(beforePreview.counters.downloads ?? 0);

    targetSlug = await createSync(page, 'send', 'OneDrive', `UI OneDrive Export ${Date.now()}`);
    await openAccount(page);
    const targetAccount = page.locator('#sync-step-account');
    await expect(targetAccount).toContainText('Also used by 1 other sync');
    await expect(targetAccount).toContainText('needs more access');

    await signInToOneDrive(page, targetSlug, 'Files.ReadWrite');
    await openAccount(page);
    await expect(targetAccount).toContainText('Signed in');

    await page.goto(`/utilities/automations/${sourceSlug}`);
    await openAccount(page);
    await expect(page.locator('#sync-step-account')).toContainText('Signed in');
    await expect(page.locator('#sync-step-account')).toContainText('Also used by 1 other sync');
  } finally {
    if (targetSlug) await deleteSync(page, targetSlug);
    await deleteSync(page, sourceSlug);
  }
});

test('onedrive_target_reviews_destination_and_selection_changes', async ({ page }) => {
  const slug = await createSync(page, 'send', 'OneDrive', `UI OneDrive Review ${Date.now()}`);
  try {
    await signInToOneDrive(page, slug, 'Files.ReadWrite');
    const destination = page.locator('[data-remote-option="folder"]');
    await destination.locator('[data-remote-open]').first().click();
    const cameraRoll = destination.locator('[data-remote-list] .sync-picker-item').filter({ hasText: 'Camera Roll' });
    await expect(cameraRoll).toBeVisible();
    await Promise.all([
      page.waitForResponse(response => response.url().includes(`/utilities/automations/${slug}/sync/options`)
        && response.request().method() === 'POST'),
      cameraRoll.getByRole('button', { name: /Choose/ }).click(),
    ]);
    const albumCount = await chooseAlbum(page);
    expect(albumCount).toBeGreaterThan(0);

    await page.locator('#sync-enable-review').click();
    const review = page.locator('#syncEnableModal.active');
    await expect(review).toBeVisible();
    await expect(review).toContainText('Seeded Album');
    await expect(review).toContainText('jason@example.com');
    await expect(review).toContainText('Camera Roll');
    await expect(review).toContainText('deletes its unchanged copy from OneDrive');
    await Promise.all([
      page.waitForResponse(response => response.url().includes(`/utilities/automations/${slug}/sync/turn-on`)
        && response.request().method() === 'POST'),
      review.locator('button[type="submit"]').click(),
    ]);
    await expect(page.locator('.automation-actions [role="switch"]')).toHaveAttribute('aria-checked', 'true');

    await page.locator('#sync-step-where > summary').click();
    await page.locator('#sync-selection-query').check();
    await page.locator('#sync-step-where [data-selection-submit]').click();
    await expect(page).toHaveURL(new RegExp(`/utilities/automations/${slug}/sync/filter`));
    await chooseSearchableOption(page, '#media-type-select', 'Photos Only');
    await page.getByRole('button', { name: 'Apply Filters' }).click();
    await page.getByRole('button', { name: 'Send these photos' }).click();
    await expect(page).toHaveURL(new RegExp(`/utilities/automations/${slug}$`));
    await expect(page.locator('.automation-actions [role="switch"]')).toHaveAttribute('aria-checked', 'false');
    await page.locator('#sync-enable-review').click();
    await expect(page.locator('#syncEnableModal.active')).toContainText('photos matching your filter');
  } finally {
    await deleteSync(page, slug);
  }
});

test('send_album_to_folder_runs_and_requires_consent_after_a_change', async ({ page }, testInfo) => {
  const slug = await createSync(page, 'send', 'Folder', `UI Sync Export ${Date.now()}`);
  try {
    const destination = await createDestination(page, testInfo, slug);
    await chooseSearchableOption(page, '#sync-option-convert', 'Yes');
    await saveFolderOptions(page, slug);
    await expect(page.locator('#sync-option-folder')).toHaveValue(/sync-destination-/);

    const albumCount = await chooseAlbum(page);
    expect(albumCount).toBeGreaterThan(0);
    await page.locator('#sync-test').click();
    const preview = page.locator('#sync-test-card');
    await expect(preview).toBeVisible();
    await expect(preview).toContainText('nothing was copied or changed');
    await expect(preview.locator('.sync-test-stat')).toContainText('to copy');
    expect(await preview.locator('.sync-test-table tbody tr').count()).toBeGreaterThan(0);

    const triggers = page.locator('#automation-trigger-panel');
    await triggers.locator('summary').click();
    await expect(triggers.locator('.automation-trigger-desc')).toHaveAttribute('data-cron', '0 2 * * *');
    await expect(triggers.locator('.automation-trigger-event').filter({ hasText: 'Album changed' })).toBeVisible();

    await enableSync(page, slug);
    await page.locator('#sync-run-now').click();
    const run = page.locator('#automation-runs .run-history-row').first();
    await expect(run).toContainText(new RegExp(`Wrote ${albumCount} photos to the folder`), { timeout: RUN_TIMEOUT });
    await expect(run.locator('.chip')).toContainText(/Completed|Done/);
    // The run says it wrote them; check they're really in the folder. "Convert to JPEG"
    // accepts JPEG and PNG as they are (the fixture is PNG) and converts anything else.
    const written = readdirSync(destination, { recursive: true, withFileTypes: true })
      .filter((entry) => entry.isFile() && !entry.name.startsWith('.'));
    expect(written).toHaveLength(albumCount);
    expect(written.map((entry) => entry.name).filter((name) => !/\.(jpe?g|png)$/i.test(name))).toEqual([]);

    await page.reload();
    const items = page.locator('#sync-items');
    await items.locator('summary').click();
    await expect(items.locator('.sync-items-table tbody tr')).toHaveCount(albumCount, { timeout: RUN_TIMEOUT });
    await expect(items.locator('.sync-items-table')).toContainText('Written to folder');

    await chooseFolder(page, '#sync-option-folder', testInfo.outputPath());
    await saveFolderOptions(page, slug);
    await expect(page.locator('.automation-actions [role="switch"]')).toHaveAttribute('aria-checked', 'false');
    await expect(page.locator('.automation-actions [role="switch"]')).toBeEnabled();
    await expect(page.locator('#sync-run-now')).toHaveCount(0);
  } finally {
    await deleteSync(page, slug);
  }
});

test('folder_inside_library_cannot_be_a_send_destination', async ({ page }) => {
  await page.goto('/settings');
  const libraryPath = (await page.locator('.media-dir-path').first().textContent())?.trim();
  expect(libraryPath).toBeTruthy();
  const slug = await createSync(page, 'send', 'Folder', `UI Sync Guard ${Date.now()}`);
  try {
    await page.locator('#sync-option-folder').fill(libraryPath!);
    await saveFolderOptions(page, slug);
    await expect(page.locator('#sync-option-folder-problem')).toContainText('That folder is inside your library. Choose one outside it.');
    await expect(page.locator('.automation-actions [role="switch"]')).toBeDisabled();
    await expect(page.locator('#sync-test')).toHaveCount(0);
  } finally {
    await deleteSync(page, slug);
  }
});

test('send_photos_matching_a_filter_saves_a_query_selection', async ({ page }) => {
  const slug = await createSync(page, 'send', 'Folder', `UI Sync Filter ${Date.now()}`);
  try {
    await page.locator('#sync-selection-query').check();
    await page.locator('#sync-step-where [data-selection-submit]').click();
    await expect(page).toHaveURL(new RegExp(`/utilities/automations/${slug}/sync/filter`));

    await chooseSearchableOption(page, '#media-type-select', 'Photos Only');
    await page.getByRole('button', { name: 'Apply Filters' }).click();
    await expect(page).toHaveURL(/[?&]media-type=photo/);
    await expect(page.locator('.page-header .chip-accent')).toContainText(/\d+ photos? match/);
    await expect(page.getByRole('button', { name: 'Send these photos' })).toBeEnabled();
    await page.getByRole('button', { name: 'Send these photos' }).click();
    await expect(page).toHaveURL(new RegExp(`/utilities/automations/${slug}$`));
    await expect(page.locator('#sync-step-where .sync-step-value')).toContainText('Photos matching a filter');
  } finally {
    await deleteSync(page, slug);
  }
});

test('import_from_folder_previews_and_runs_fixture_photos', async ({ page }) => {
  const slug = await createSync(page, 'import', 'Folder', `UI Sync Import ${Date.now()}`);
  try {
    const sourcePath = join(process.cwd(), 'test_data', 'photos');
    await page.locator('#sync-option-folder').fill(sourcePath);
    await saveFolderOptions(page, slug);
    await expect(page.locator('#sync-subpath')).toHaveValue(/Imports\/UI Sync Import/);

    await page.locator('#sync-test').click();
    const preview = page.locator('#sync-test-card');
    await expect(preview).toBeVisible();
    await expect(preview.locator('.sync-test-table tbody tr').first()).toBeVisible();
    await expect(preview).toContainText(/New|Already in your library/);

    await enableSync(page, slug);
    await page.locator('#sync-run-now').click();
    const run = page.locator('#automation-runs .run-history-row').first();
    await expect(run).toContainText(/Imported \d+ new photos/, { timeout: RUN_TIMEOUT });
    await expect(run.locator('.chip')).toContainText(/Completed|Done/);
    await page.reload();
    const items = page.locator('#sync-items');
    await items.locator('summary').click();
    await expect(items.locator('.sync-items-table tbody tr').first()).toBeVisible({ timeout: RUN_TIMEOUT });
  } finally {
    await deleteSync(page, slug);
  }
});
