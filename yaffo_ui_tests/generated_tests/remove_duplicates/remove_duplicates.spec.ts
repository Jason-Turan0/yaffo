import { test, expect, Page, Locator } from '@playwright/test';
import { buildDuplicateImageCorpus, countEntriesIn, removeTempDirs } from '../_support/sandbox-fs';

const UNIQ = Date.now();
const GROUP_COUNT = 12; // > page size (10) so pagination is exercised

// The suite builds its own duplicate corpus in a scratch directory (never the
// shared library) and moves duplicates to a scratch destination, so the sandbox
// stays untouched. Serial: one find_duplicates job feeds all scenarios.
test.describe.configure({ mode: 'serial', timeout: 300_000 });

let scanDir: string;
let destDir: string;
let jobId: string;

async function sandboxRoot(page: Page): Promise<string> {
  await page.goto('/settings');
  const dbPath = (await page.locator('.system-path-item')
    .filter({ has: page.getByText('Database Path:', { exact: true }) })
    .locator('code').first().textContent())!.trim();
  return dbPath.replace(/\/[^/]+$/, '');
}

// GROUP_COUNT pairs of identical files. Duplicate detection groups by exact
// perceptual hash (imagehash.phash), so each group's source must be VISUALLY
// distinct — copies of one image with different bytes appended all land in a
// single group. The _support helper copies GROUP_COUNT distinct test_data
// photographs into the scan dir, each twice (real photos have distinct phashes).
function buildDuplicateCorpus(): void {
  buildDuplicateImageCorpus(scanDir, destDir, GROUP_COUNT);
}

function headerStat(page: Page, label: string): Locator {
  return page.locator('#duplicates-header .stat-card')
    .filter({ has: page.getByText(label, { exact: true }) })
    .locator('.stat-value');
}

async function openResults(page: Page): Promise<void> {
  await page.goto(`/utilities/remove-duplicates/results/${jobId}`);
  await expect(page.locator('#duplicates-form')).toBeVisible();
}

async function pickSearchableOption(page: Page, selectSelector: string, optionText: string): Promise<void> {
  const wrapper = page.locator(`${selectSelector} + .searchable-select-wrapper`);
  await wrapper.locator('.searchable-select-display').click();
  await wrapper.locator('.searchable-select-option').filter({ hasText: optionText }).first().click();
}

test.describe('Remove Duplicates', () => {
  test.afterAll(() => {
    removeTempDirs(scanDir, destDir);
  });

  test('remove_duplicates_scan_finds_groups', async ({ page }) => {
    const root = await sandboxRoot(page);
    scanDir = `${root}/spec-dup-scan-${UNIQ}`;
    destDir = `${root}/spec-dup-dest-${UNIQ}`;
    buildDuplicateCorpus();

    await page.goto('/utilities/remove-duplicates');
    await expect(page.locator('.page-header')).toContainText('Remove Duplicates');

    // The page starts with no directory rows; add one, then entering a directory
    // triggers an HTMX rescan that refreshes the media count.
    await expect(page.locator('#directories-container')).toContainText('No directory selected');
    await page.locator('#add-directory-button').click();
    const dirInput = page.locator('#remove-duplicates-form input[name="directory"]').first();
    await expect(dirInput).toBeVisible();
    await dirInput.fill(scanDir);
    // The input rescans on its change event (hx-trigger="change"); dispatch it
    // explicitly — blur alone is not a reliable trigger under automation.
    await Promise.all([
      page.waitForResponse(response => response.url().includes('/utilities/remove-duplicates-form')),
      dirInput.dispatchEvent('change'),
    ]);
    await expect(page.locator('#remove-duplicates-form .stat-card')
      .filter({ has: page.getByText('Total Media', { exact: true }) })
      .locator('.stat-value')).toHaveText(String(GROUP_COUNT * 2));

    // Start the scan. The page reloads right after the 202 (which drops the
    // response body), so read the job id from the job card the reloaded page
    // renders. Completed find_duplicates jobs from previous runs linger on the
    // page, so diff the card ids before/after to find OUR job.
    const cardIds = async () => new Set(
      await page.locator('#job-progress-section [id^="job-"]').evaluateAll(
        cards => cards.map(card => card.id)));
    const before = await cardIds();
    await page.locator('#find-duplicates-button').click();
    await expect(async () => {
      const fresh = [...await cardIds()].filter(id => !before.has(id));
      expect(fresh).toHaveLength(1);
      jobId = fresh[0].replace(/^job-/, '');
    }).toPass({ timeout: 30_000 });
    expect(jobId).toBeTruthy();

    // Hashing runs behind the shared worker; poll the results page until every
    // group is in.
    await expect(async () => {
      await openResults(page);
      await expect(headerStat(page, 'Duplicate Groups Found')).toHaveText(String(GROUP_COUNT), { timeout: 1000 });
    }).toPass({ timeout: 180_000, intervals: [2_000] });

    // Each group shows both copies with the first kept (unselected) and the rest
    // marked for removal (selected).
    await expect(headerStat(page, 'Total Media Processed')).toHaveText(String(GROUP_COUNT * 2));
    await expect(headerStat(page, 'Duplicates Selected')).toHaveText(String(GROUP_COUNT));
    const firstGroup = page.locator('.duplicate-group').first();
    await expect(firstGroup.locator('.photo-card')).toHaveCount(2);
    await expect(firstGroup.locator('.photo-card').first()).not.toHaveClass(/selected/);
    await expect(firstGroup.locator('.photo-card').nth(1)).toHaveClass(/selected/);
  });

  test('remove_duplicates_review_and_select_photos', async ({ page }) => {
    await openResults(page);

    // Toggling a kept photo marks it for removal and updates the selected count.
    const keptCard = page.locator('.duplicate-group').first().locator('.photo-card').first();
    const cardId = await keptCard.getAttribute('id');
    await keptCard.click();
    await expect(page.locator(`#${cardId}`)).toHaveClass(/selected/);
    await expect(headerStat(page, 'Duplicates Selected')).toHaveText(String(GROUP_COUNT + 1));

    // Toggling back restores the original selection.
    await page.locator(`#${cardId}`).click();
    await expect(page.locator(`#${cardId}`)).not.toHaveClass(/selected/);
    await expect(headerStat(page, 'Duplicates Selected')).toHaveText(String(GROUP_COUNT));

    // Pagination: 12 groups at page size 10 → two pages; selections are carried
    // in the form and survive the page change.
    await expect(page.locator('.results-count')).toContainText(`of ${GROUP_COUNT} results`);
    await page.locator('.page-navigation .page-btn', { hasText: 'Next' }).click();
    await expect(page.locator('.page-info')).toContainText('Page 2 of 2');
    await expect(page.locator('.duplicate-group')).toHaveCount(GROUP_COUNT - 10);
    await expect(headerStat(page, 'Duplicates Selected')).toHaveText(String(GROUP_COUNT));
    await page.locator('.page-navigation .page-btn', { hasText: 'First' }).click();
    await expect(page.locator('.page-info')).toContainText('Page 1 of 2');
  });

  test('remove_duplicates_change_action_type', async ({ page }) => {
    await openResults(page);

    // Default action is trash; no destination input is shown.
    await expect(page.locator('#action-type')).toHaveValue('trash');
    await expect(page.locator('#destination-folder')).toHaveCount(0);

    // Switching to Move to Folder re-renders the header with a destination input.
    await pickSearchableOption(page, '#action-type', 'Move to Folder');
    await expect(page.locator('#action-type')).toHaveValue('moveFolder');
    const destination = page.locator('#destination-folder');
    await expect(destination).toBeVisible();

    await destination.fill(destDir);
    await destination.blur();
    // The header round-trips through the server keeping the chosen action state.
    await expect(page.locator('#action-type')).toHaveValue('moveFolder');
    await expect(page.locator('#destination-folder')).toHaveValue(destDir);

    // And back to trash hides the destination again.
    await pickSearchableOption(page, '#action-type', 'Move to Trash');
    await expect(page.locator('#action-type')).toHaveValue('trash');
    await expect(page.locator('#destination-folder')).toHaveCount(0);
  });

  test('remove_duplicates_execute_removal', async ({ page }) => {
    await openResults(page);

    // Execute as move-to-folder so the result is observable and restorable.
    await pickSearchableOption(page, '#action-type', 'Move to Folder');
    const destination = page.locator('#destination-folder');
    await destination.fill(destDir);
    await destination.blur();
    await expect(page.locator('#destination-folder')).toHaveValue(destDir);

    // Note: the app currently offers NO confirmation dialog before executing —
    // the button posts directly. The spec's confirmation expectation is a product
    // gap (flagged), so the test asserts the actual flow.
    const removeButton = page.locator('#duplicates-header .btn-danger', { hasText: 'Remove Selected Duplicates' });
    await expect(removeButton).toBeEnabled();
    await Promise.all([
      page.waitForResponse(response =>
        response.url().includes(`/utilities/remove-duplicates/execute/${jobId}`) && response.ok()),
      removeButton.click(),
    ]);

    // The response fires a "Started removing N duplicates" toast and an
    // HX-Redirect; the redirect wipes the toast almost immediately (same pattern
    // the face_assignment suite documents), so the durable outcomes — the
    // redirect and the moved files — are what get asserted.
    await page.waitForURL(/\/utilities\/remove-duplicates$/);

    // The marked copies land in the destination folder; one copy per group stays.
    await expect(async () => {
      expect(countEntriesIn(destDir)).toBe(GROUP_COUNT);
      expect(countEntriesIn(scanDir)).toBe(GROUP_COUNT);
    }).toPass({ timeout: 120_000, intervals: [2_000] });
  });
});
