import { test, expect, Page, Locator } from '@playwright/test';

const UNIQ = Date.now();
const SPEC_LABEL = `spec-set-label-${UNIQ}`;

// The suite mutates global application settings (locale, units, directories), so it
// runs serially and restores every setting it changes.
test.describe.configure({ mode: 'serial', timeout: 90_000 });

async function openSettings(page: Page): Promise<void> {
  await page.goto('/settings');
  await expect(page.locator('.page-header')).toBeVisible();
}

function notification(page: Page): Locator {
  return page.locator('.notification.visible');
}

// Pick an option from the searchable-select widget wrapping a hidden native select.
async function pickSearchableOption(page: Page, selectSelector: string, optionText: string): Promise<void> {
  const wrapper = page.locator(`${selectSelector} + .searchable-select-wrapper`);
  await wrapper.locator('.searchable-select-display').click();
  await wrapper.locator('.searchable-select-option').filter({ hasText: optionText }).first().click();
}

// The sandbox root, derived from the Database Path shown in System Information
// (<root>/yaffo.db) — scratch directories for these tests live under it.
async function sandboxRoot(page: Page): Promise<string> {
  await openSettings(page);
  const dbPath = (await page.locator('.system-path-item')
    .filter({ has: page.getByText('Database Path:', { exact: true }) })
    .locator('code').first().textContent())!.trim();
  return dbPath.replace(/\/[^/]+$/, '');
}

function mediaDirItem(page: Page, path: string): Locator {
  return page.locator('.media-dir-item').filter({ hasText: path });
}

async function removeMediaDirViaUI(page: Page, path: string): Promise<void> {
  const item = mediaDirItem(page, path);
  if (await item.count() === 0) return;
  await item.locator('[data-action="remove-media-dir"]').click();
  await expect(page.locator('#global-confirm-dialog')).toHaveClass(/active/);
  await page.locator('#confirm-dialog-confirm').click();
  await expect(mediaDirItem(page, path)).toHaveCount(0);
}

test.describe('Settings', () => {
  test('settings_add_media_directory', async ({ page }) => {
    const scratchDir = `${await sandboxRoot(page)}/spec-test-media-${UNIQ}`;

    // An empty path is rejected with a validation error instead of being added.
    await page.locator('[data-action="add-media-dir"]').click();
    await expect(notification(page)).toContainText('Please enter a directory path');

    // A valid path is created and appears in the list (the server mkdirs it).
    await page.locator('#new-media-dir').fill(scratchDir);
    await Promise.all([
      page.waitForResponse(response =>
        response.url().includes('/api/settings/media-dirs') && response.request().method() === 'POST'),
      page.locator('[data-action="add-media-dir"]').click(),
    ]);
    await expect(notification(page)).toContainText('Media directory added successfully');
    await expect(mediaDirItem(page, scratchDir)).toHaveCount(1);

    // Cleanup: remove the scratch directory entry.
    await removeMediaDirViaUI(page, scratchDir);
  });

  test('settings_remove_media_directory', async ({ page, request }) => {
    // Never remove the seeded library directory — other suites depend on it. Add a
    // scratch directory and remove that one.
    const scratchDir = `${await sandboxRoot(page)}/spec-test-remove-${UNIQ}`;
    const response = await request.post('/api/settings/media-dirs', { data: { directory: scratchDir } });
    expect(response.ok()).toBeTruthy();

    await openSettings(page);
    const seededCount = await page.locator('.media-dir-item').count();
    await mediaDirItem(page, scratchDir).locator('[data-action="remove-media-dir"]').click();

    // A confirmation dialog naming the directory precedes the removal.
    const dialog = page.locator('#global-confirm-dialog');
    await expect(dialog).toHaveClass(/active/);
    await expect(dialog).toContainText(scratchDir);
    await page.locator('#confirm-dialog-confirm').click();

    await expect(notification(page)).toContainText(`Removed: ${scratchDir}`);
    await expect(mediaDirItem(page, scratchDir)).toHaveCount(0);
    await expect(page.locator('.media-dir-item')).toHaveCount(seededCount - 1);

    // Note: the "No media directories configured" empty state requires removing the
    // seeded library directory, which would break the rest of the suite — not asserted.
  });

  test('settings_change_language', async ({ page }) => {
    await openSettings(page);
    const original = await page.locator('#application-locale').inputValue();
    const target = original === 'es' ? 'en' : 'es';
    const targetLabel = target === 'es' ? 'Español' : 'English';
    const localeSubmit = page.locator('form[action$="/settings/locale"] button[type="submit"]');

    try {
      await pickSearchableOption(page, '#application-locale', targetLabel);
      await localeSubmit.click();

      // The UI re-renders in the chosen locale (html lang + translated text).
      // IMPORTANT: the locale is a GLOBAL setting — while it is non-English,
      // every parallel suite that server-renders English text can fail. Keep
      // this window to the single redirect render: no extra navigations here.
      await expect(page.locator('html')).toHaveAttribute('lang', target);
      if (target === 'es') {
        await expect(page.locator('.page-header')).toContainText('Ajustes');
      }
    } finally {
      // Restore immediately via a direct form POST.
      await page.request.post('/settings/locale', { form: { locale: original } });
    }

    // The saved locale persists across navigation — verified with the restored
    // locale (same read-the-setting-per-request mechanism, zero Spanish window).
    await page.goto('/people');
    await expect(page.locator('html')).toHaveAttribute('lang', original);
    await openSettings(page);
    await expect(page.locator('#application-locale')).toHaveValue(original);
  });

  test('settings_change_distance_unit', async ({ page }) => {
    await openSettings(page);
    const original = await page.locator('#distance-unit').inputValue();
    const target = original === 'km' ? 'mi' : 'km';
    const labels: Record<string, string> = { km: 'Kilometers', mi: 'Miles' };
    const unitSubmit = page.locator('form[action$="/settings/distance-unit"] button[type="submit"]');

    try {
      await pickSearchableOption(page, '#distance-unit', labels[target]);
      await unitSubmit.click();

      // The preference is saved and survives a reload.
      await expect(page.locator('#distance-unit')).toHaveValue(target);
      await page.reload();
      await expect(page.locator('#distance-unit')).toHaveValue(target);

      // Note: distances rendered elsewhere (locations, automation distance fields)
      // read this preference live; asserting them here would couple this suite to
      // those pages, so persistence is the contract verified.
    } finally {
      await openSettings(page);
      await pickSearchableOption(page, '#distance-unit', labels[original]);
      await unitSubmit.click();
      await expect(page.locator('#distance-unit')).toHaveValue(original);
    }
  });

  test('settings_change_thumbnail_directory', async ({ page }) => {
    await openSettings(page);
    const originalDir = (await page.locator('#current-thumbnail-dir').textContent())!.trim();
    const newDir = `${originalDir}-spec-${UNIQ}`;

    // The thumbnail stats stream fills the file count and total size on load.
    await expect(page.locator('#thumbnail-count')).not.toHaveText('…', { timeout: 20_000 });
    await expect(page.locator('#thumbnail-size')).not.toHaveText(/Counting…/, { timeout: 20_000 });

    const moveTo = async (destination: string) => {
      await openSettings(page);
      await page.locator('#new-thumbnail-dir').fill(destination);
      await page.locator('[data-action="change-thumbnail-dir"]').click();

      // The confirmation dialog reports how many files and how much data will move.
      const dialog = page.locator('#global-confirm-dialog');
      await expect(dialog).toHaveClass(/active/);
      await expect(dialog).toContainText(`New location: ${destination}`);
      await expect(dialog).toContainText(/This will move [\d,.]+ files? \([\d,.]+ \w+\)/);

      await Promise.all([
        page.waitForResponse(response =>
          response.url().includes('/api/settings/thumbnail-dir') && response.ok()),
        page.locator('#confirm-dialog-confirm').click(),
      ]);
      // The success toast fires and the page reloads with the new current directory.
      await expect(page.locator('#current-thumbnail-dir')).toHaveText(destination, { timeout: 20_000 });
    };

    try {
      await moveTo(newDir);
    } finally {
      await moveTo(originalDir);
    }
  });

  test('settings_manage_classification_labels', async ({ page }) => {
    await openSettings(page);
    const section = page.locator('#labels-section');
    await expect(section).toBeVisible();

    // Add a new label to the vocabulary (the section swaps in place on save).
    await section.locator('input[name="name"]').fill(SPEC_LABEL);
    await Promise.all([
      page.waitForResponse(response =>
        response.url().includes('/settings/labels') && response.request().method() === 'POST'),
      section.locator('.add-label-form button[type="submit"]').click(),
    ]);
    const chip = page.locator('#labels-section .label-chip').filter({ hasText: SPEC_LABEL });
    await expect(chip).toHaveCount(1);

    // Removing it takes it back out of the vocabulary.
    await Promise.all([
      page.waitForResponse(response =>
        response.url().includes('/settings/labels') && response.request().method() === 'POST'),
      chip.locator('.label-chip-remove').click(),
    ]);
    await expect(page.locator('#labels-section .label-chip').filter({ hasText: SPEC_LABEL })).toHaveCount(0);
  });

  test('settings_system_information_displayed', async ({ page }) => {
    await openSettings(page);

    const expectedEntries = [
      'Build Version:',
      'Build Timestamp:',
      'Database Path:',
      'Task Queue Database Path:',
      'ExifTool Path:',
      'FFmpeg Path:',
      'Image Classification Model:',
      'Face Recognition Model:',
    ];
    for (const label of expectedEntries) {
      // Exact label match — "Database Path:" is a substring of "Task Queue Database Path:".
      const item = page.locator('.system-path-item').filter({ has: page.getByText(label, { exact: true }) });
      await expect(item).toHaveCount(1);
      await expect(item.locator('code').first()).not.toHaveText('');
    }
  });
});
