import { test, expect, Page } from '@playwright/test';

// Albums suite — runs against the standard single-instance sandbox (BASE_URL).
// There are NO paired devices here: the album share/un-share flow lives in the
// sharing suite (sharing_album_share_modal_toggle); this file only checks the
// Share modal's empty state.
//
// The tests create/rename/delete albums and edit the seeded album's membership,
// cover, and order — shared server state — so the file runs sequentially in one
// worker. Each test restores what it changed (except the cover, which nothing
// else depends on).
test.describe.configure({ mode: 'default' });

const SEEDED_ALBUM = 'Seeded Album';
const SEEDED_MEMBER_COUNT = 4;

// The app-wide confirm modal (components/confirm-dialog.js) — albums.js opens it
// for delete/remove; no native window.confirm is involved.
async function acceptConfirmDialog(page: Page, expectedText?: RegExp): Promise<void> {
  const dialog = page.locator('#global-confirm-dialog.active');
  await expect(dialog).toBeVisible();
  if (expectedText) {
    await expect(dialog).toContainText(expectedText);
  }
  await page.locator('#confirm-dialog-confirm').click();
}

// Selects an option in the custom searchable-select widget that wraps a native
// <select> (the native element is hidden, so locator.selectOption won't work).
async function pickSearchableOption(page: Page, selectSelector: string, optionText: string | RegExp): Promise<void> {
  const wrapper = page.locator(`${selectSelector} + .searchable-select-wrapper`);
  await wrapper.locator('.searchable-select-display').click();
  await wrapper.locator('.searchable-select-option').filter({ hasText: optionText }).first().click();
}

// Open an album from the sidebar by name; returns its path ("/albums/<id>").
async function openAlbum(page: Page, name: string): Promise<string> {
  await page.goto('/albums');
  await page.locator('.albums-sidebar .panel-nav a', { hasText: name }).first().click();
  await page.waitForURL(/\/albums\/\d+$/);
  return new URL(page.url()).pathname;
}

// The album page's member count, from the "N photos" header subtitle.
async function headerPhotoCount(page: Page): Promise<number> {
  const text = (await page.locator('.page-header .subtitle').textContent()) || '';
  const match = text.match(/([\d,]+)\s+photos/);
  expect(match, `Expected a member count in "${text}"`).not.toBeNull();
  return Number(match![1].replace(/,/g, ''));
}

async function memberIds(page: Page, albumPath: string): Promise<string[]> {
  await page.goto(albumPath);
  return page.locator('#album-grid .photo-card[data-select-id]').evaluateAll(
    (cards) => cards.map((card) => card.getAttribute('data-select-id') || ''));
}

// Remove members via edit mode; the selection rides the URL, so navigate with
// select_id params instead of clicking cards.
async function removeMembers(page: Page, albumPath: string, ids: string[]): Promise<void> {
  if (!ids.length) return;
  const params = ids.map((id) => `select_id=${id}`).join('&');
  await page.goto(`${albumPath}?edit=1&${params}`);
  await page.locator('#remove-from-album-button').click();
  await acceptConfirmDialog(page, /not deleted/);
  await page.waitForURL(/edit=1/);
}

// Reorder album members by posting the new order via browser-side fetch, which
// automatically includes the CSRF token (via security.js wrapper).
async function reorderMembers(page: Page, albumPath: string, orderedIds: string[]): Promise<void> {
  await page.evaluate(async ({ url, ids }) => {
    const body = new FormData();
    ids.forEach((id) => body.append('media_item_id', id));
    const response = await fetch(url, { method: 'POST', body });
    if (response.status !== 204) {
      throw new Error(`Reorder failed with status ${response.status}`);
    }
  }, { url: `${albumPath}/reorder`, ids: orderedIds });
}

test.describe('Albums Feature', () => {

  test.beforeEach(async () => {
    test.setTimeout(30_000);
  });

  test('albums_overview_lists_albums_as_tiles - The albums tab opens on an overview of tiles with covers and counts', async ({ page }) => {
    await page.goto('/albums');

    // One tile per album, with cover image, name, and count
    const tile = page.locator('.album-tile', { hasText: SEEDED_ALBUM });
    await expect(tile).toHaveCount(1);
    await expect(tile.locator('.album-tile-cover img')).toBeVisible();
    await expect(tile.locator('.album-tile-meta .chip').first()).toHaveText(new RegExp(`${SEEDED_MEMBER_COUNT} photos`));

    // The sidebar lists it with a count chip
    const sidebarEntry = page.locator('.albums-sidebar .panel-nav a', { hasText: SEEDED_ALBUM });
    await expect(sidebarEntry).toHaveCount(1);
    await expect(sidebarEntry.locator('.chip').last()).toHaveText(String(SEEDED_MEMBER_COUNT));

    // Clicking the tile opens the album
    await tile.click();
    await page.waitForURL(/\/albums\/\d+$/);
    await expect(page.locator('.page-header h1')).toContainText(SEEDED_ALBUM);
    await expect(page.locator('#album-grid .photo-card')).toHaveCount(SEEDED_MEMBER_COUNT);
  });

  test('albums_create_and_delete - The user can create an album and delete it again', async ({ page }) => {
    await page.goto('/albums');

    // Create via the New album modal
    await page.locator('#new-album-button').click();
    const modal = page.locator('#newAlbumModal');
    await expect(modal).toBeVisible();
    await page.locator('#new-album-name').fill('Create Delete E2E');
    await page.locator('#new-album-description').fill('Throwaway album for the create/delete test');
    await modal.locator('button[type="submit"]').click();
    await page.waitForURL(/\/albums\/\d+$/);

    // It opens on its own page, empty, pointing at "Add photos"
    await expect(page.locator('.page-header h1')).toContainText('Create Delete E2E');
    const emptyState = page.locator('.empty-state');
    await expect(emptyState).toContainText('This album is empty');
    await expect(emptyState.locator('a', { hasText: 'Add photos' })).toBeVisible();
    await expect(page.locator('.albums-sidebar .panel-nav a', { hasText: 'Create Delete E2E' })).toHaveCount(1);

    // Delete asks for confirmation and says the photos are not deleted
    await page.locator('#delete-album-button').click();
    await acceptConfirmDialog(page, /not deleted/);
    await page.waitForURL(/\/albums$/);

    // Gone from the sidebar and the overview; the library is untouched
    await expect(page.locator('.albums-sidebar .panel-nav a', { hasText: 'Create Delete E2E' })).toHaveCount(0);
    await expect(page.locator('.album-tile', { hasText: 'Create Delete E2E' })).toHaveCount(0);
    await page.goto('/?view=grid');
    await expect(page.locator('.photo-card').first()).toBeVisible();
  });

  test('albums_edit_details - The user can rename an album, and clashing names are rejected', async ({ page }) => {
    const albumPath = await openAlbum(page, SEEDED_ALBUM);

    // The modal opens prefilled with the current name and description
    await page.locator('#edit-album-details-button').click();
    const modal = page.locator('#editAlbumModal');
    await expect(modal).toBeVisible();
    await expect(page.locator('#edit-album-name')).toHaveValue(SEEDED_ALBUM);
    await expect(page.locator('#edit-album-description')).toHaveValue('Seeded for UI tests');

    // Rename it
    await page.locator('#edit-album-name').fill('Renamed Album E2E');
    await page.locator('#edit-album-description').fill('Renamed by the edit-details test');
    await modal.locator('button[type="submit"]').click();
    await page.waitForURL(albumPath);
    await expect(page.locator('.page-header h1')).toContainText('Renamed Album E2E');
    await expect(page.locator('.albums-sidebar .panel-nav a', { hasText: 'Renamed Album E2E' })).toHaveCount(1);

    // A name clashing with another album is rejected
    await page.locator('#new-album-button').click();
    await expect(page.locator('#newAlbumModal')).toBeVisible();
    await page.locator('#new-album-name').fill('Clash Album E2E');
    await page.locator('#newAlbumModal button[type="submit"]').click();
    await page.waitForURL(/\/albums\/\d+$/);
    await page.locator('#edit-album-details-button').click();
    await expect(modal).toBeVisible();
    await page.locator('#edit-album-name').fill('Renamed Album E2E');
    await modal.locator('button[type="submit"]').click();
    await expect(page.locator('.form-error')).toBeVisible();

    // Cleanup: delete the clash album, restore the seeded album's details
    await page.locator('#delete-album-button').click();
    await acceptConfirmDialog(page);
    await page.waitForURL(/\/albums$/);
    await page.goto(albumPath);
    await page.locator('#edit-album-details-button').click();
    await expect(modal).toBeVisible();
    await page.locator('#edit-album-name').fill(SEEDED_ALBUM);
    await page.locator('#edit-album-description').fill('Seeded for UI tests');
    await modal.locator('button[type="submit"]').click();
    await page.waitForURL(albumPath);
    await expect(page.locator('.page-header h1')).toContainText(SEEDED_ALBUM);
  });

  test('albums_add_photos_by_selection - Two ticked photos are added, and the screen stays on the add screen', async ({ page }) => {
    const albumPath = await openAlbum(page, SEEDED_ALBUM);
    const before = await memberIds(page, albumPath);
    await page.goto(albumPath);
    await page.locator('.page-header a', { hasText: 'Add photos' }).click();
    await page.waitForURL(/\/albums\/\d+\/add/);

    // Filter sidebar beside an always-selecting grid; the running total chip
    await expect(page.locator('#filter-form')).toBeAttached();
    await expect(page.locator('#add-grid.is-selecting')).toBeVisible();
    await expect(page.locator('.page-header .chip-accent')).toContainText(`${before.length} in this album`);

    // Tick two photos: the count follows, the selection rides the URL
    const cards = page.locator('#add-grid .photo-card');
    await cards.nth(0).click();
    await cards.nth(1).click();
    await expect(page.locator('#add-selection [data-selection-count]')).toHaveText(/\b2 selected/);
    const pickedIds = [
      await cards.nth(0).getAttribute('data-select-id'),
      await cards.nth(1).getAttribute('data-select-id'),
    ];

    // Add: STAYS on the add screen, confirms the count, drops the new members
    await page.locator('#add-to-album-button').click();
    await page.waitForURL(/[?&]added=2/);
    await expect(page.locator('.add-photos-confirmation')).toHaveText(/2 photos added/);
    await expect(page.locator('.page-header .chip-accent')).toContainText(`${before.length + 2} in this album`);
    for (const id of pickedIds) {
      await expect(page.locator(`#add-grid .photo-card[data-select-id="${id}"]`)).toHaveCount(0);
    }

    // The album header count went up by two
    await page.goto(albumPath);
    expect(await headerPhotoCount(page)).toBe(before.length + 2);

    // Cleanup: remove exactly what this test added
    const added = (await memberIds(page, albumPath)).filter((id) => !before.includes(id));
    await removeMembers(page, albumPath, added);
    expect(await memberIds(page, albumPath)).toHaveLength(before.length);
  });

  test('albums_bulk_add_by_filter - Every photo matching a filter is added, not just the visible page', async ({ page }) => {
    const albumPath = await openAlbum(page, SEEDED_ALBUM);
    const before = await memberIds(page, albumPath);
    await page.goto(`${albumPath}/add`);

    // The chip names the number MATCHING THE FILTER (match_count is also on the
    // add button, which avoids parsing the uppercase-transformed chip label)
    const unfilteredMatch = Number(await page.locator('#add-to-album-button').getAttribute('data-match-count'));
    expect(unfilteredMatch).toBeGreaterThan(0);

    // Apply a year filter: the count changes with the filter
    const yearToSelect = await page.locator('select#year-select option').nth(1).getAttribute('value');
    expect(yearToSelect).not.toBeNull();
    await pickSearchableOption(page, 'select#year-select', yearToSelect!);
    await page.getByRole('button', { name: 'Apply Filters' }).click();
    await page.waitForURL(new RegExp(`[?&]year=${yearToSelect}`));
    const filteredMatch = Number(await page.locator('#add-to-album-button').getAttribute('data-match-count'));
    expect(filteredMatch).toBeGreaterThan(0);
    expect(filteredMatch).toBeLessThan(unfilteredMatch);

    // Select all matching and add
    await page.locator('#add-selection [data-selection-toggle]').click();
    await expect(page.locator('#add-selection [data-selection-count]')).toHaveText(new RegExp(`\\b${filteredMatch} selected`));
    await page.locator('#add-to-album-button').click();
    await page.waitForURL(new RegExp(`[?&]added=${filteredMatch}`));

    // Every matching photo joined the album
    await page.goto(albumPath);
    expect(await headerPhotoCount(page)).toBe(before.length + filteredMatch);

    // Cleanup: remove exactly what this test added
    const added = (await memberIds(page, albumPath)).filter((id) => !before.includes(id));
    expect(added).toHaveLength(filteredMatch);
    await removeMembers(page, albumPath, added);
    expect(await memberIds(page, albumPath)).toHaveLength(before.length);
  });

  test('albums_selection_survives_pagination - A select-all-minus-one selection is kept while paging', async ({ page }) => {
    const albumPath = await openAlbum(page, SEEDED_ALBUM);
    await page.goto(`${albumPath}/add?page-size=10`);
    await expect(page.locator('#add-grid .photo-card').first()).toBeVisible();
    const matchCount = Number(await page.locator('#add-to-album-button').getAttribute('data-match-count'));
    expect(matchCount).toBeGreaterThan(10); // must actually paginate

    // Select all, untick one
    await page.locator('#add-selection [data-selection-toggle]').click();
    await expect(page.locator('#add-selection [data-selection-count]')).toHaveText(new RegExp(`\\b${matchCount} selected`));
    const unticked = page.locator('#add-grid .photo-card').first();
    const untickedId = await unticked.getAttribute('data-select-id');
    await unticked.click();
    await expect(page.locator('#add-selection [data-selection-count]')).toHaveText(new RegExp(`\\b${matchCount - 1} selected`));
    expect(page.url()).toMatch(/select=all/);
    expect(page.url()).toContain(`exclude_id=${untickedId}`);

    // Page forward and back: the selection (and the untick) survive
    await page.getByRole('link', { name: 'Next ›' }).click();
    await page.waitForURL(/[?&]page=2/);
    await expect(page.locator('#add-selection [data-selection-count]')).toHaveText(new RegExp(`\\b${matchCount - 1} selected`));
    expect(page.url()).toMatch(/select=all/);
    await page.getByRole('link', { name: '« First' }).click();
    await page.waitForURL(/[?&]page=1/);
    await expect(page.locator('#add-selection [data-selection-count]')).toHaveText(new RegExp(`\\b${matchCount - 1} selected`));
    await expect(page.locator(`#add-grid .photo-card[data-select-id="${untickedId}"]`)).not.toHaveClass(/is-selected/);
    await expect(page.locator('#add-grid .photo-card.is-selected').first()).toBeVisible();
  });

  test('albums_edit_mode_remove_and_cover - Edit mode pins a cover and removes members without deleting photos', async ({ page }) => {
    const albumPath = await openAlbum(page, SEEDED_ALBUM);
    await page.locator('.page-header a', { hasText: 'Edit' }).first().click();
    await page.waitForURL(/edit=1/);

    // Edit mode: a selecting grid and the selection bar
    await expect(page.locator('#album-grid.is-selecting')).toBeVisible();
    await expect(page.locator('#album-selection')).toBeVisible();

    // "Set as cover" needs exactly one selected photo
    const cards = page.locator('#album-grid .photo-card');
    const coverButton = page.locator('#set-cover-button');
    await cards.nth(0).click();
    await expect(coverButton).toBeEnabled();
    await cards.nth(1).click();
    await expect(coverButton).toBeDisabled();
    await cards.nth(1).click(); // back to exactly one

    const coverId = await cards.nth(0).getAttribute('data-select-id');
    await coverButton.click();
    await page.waitForURL(new RegExp(`${albumPath.replace(/\//g, '\\/')}$`));

    // The cover is marked on the grid and becomes the overview tile image
    await expect(page.locator(`#album-grid .photo-card[data-select-id="${coverId}"] .album-cover-chip`)).toBeVisible();
    await page.goto('/albums');
    const tileCoverSrc = await page.locator('.album-tile', { hasText: SEEDED_ALBUM }).locator('.album-tile-cover img').getAttribute('src');
    expect(tileCoverSrc).toContain(`/media/${coverId}`);

    // Remove a different photo (confirmation states photos are not deleted)
    await page.goto(albumPath);
    const removeId = (await memberIds(page, albumPath)).find((id) => id !== coverId)!;
    await removeMembers(page, albumPath, [removeId]);
    await expect(page.locator(`#album-grid .photo-card[data-select-id="${removeId}"]`)).toHaveCount(0);
    expect(await memberIds(page, albumPath)).toHaveLength(SEEDED_MEMBER_COUNT - 1);

    // The photo itself is still in the library
    const mediaResponse = await page.request.get(`/media/${removeId}`, { failOnStatusCode: false });
    expect(mediaResponse.ok()).toBe(true);

    // Cleanup: add it back via the add screen (selection rides the URL)
    await page.goto(`${albumPath}/add?select_id=${removeId}`);
    await page.locator('#add-to-album-button').click();
    await page.waitForURL(/[?&]added=1/);
    expect(await memberIds(page, albumPath)).toHaveLength(SEEDED_MEMBER_COUNT);
  });

  test('albums_reorder_members - Dragging a photo reorders the album and the order persists', async ({ page }) => {
    const albumPath = await openAlbum(page, SEEDED_ALBUM);
    const originalOrder = await memberIds(page, albumPath);
    expect(originalOrder.length).toBeGreaterThan(1);

    await page.goto(`${albumPath}?edit=1`);
    const cards = page.locator('#album-grid .photo-card');
    const draggedId = originalOrder[originalOrder.length - 1];
    const dragged = page.locator(`#album-grid .photo-card[data-select-id="${draggedId}"]`);

    // albums.js wires plain HTML5 drag events (dragstart moves nothing; dragover
    // on a target inserts the dragged card; dragend posts the new order)
    await dragged.dispatchEvent('dragstart');
    await cards.first().dispatchEvent('dragover');
    const reorderResponse = page.waitForResponse((response) => response.url().includes('/reorder') && response.status() === 204);
    await dragged.dispatchEvent('dragend');
    await reorderResponse;

    // The card moved immediately, and the order survives a reload
    await expect(cards.first()).toHaveAttribute('data-select-id', draggedId);
    await page.reload();
    await expect(page.locator('#album-grid .photo-card').first()).toHaveAttribute('data-select-id', draggedId);

    // Cleanup: restore the original order via browser-side fetch (includes CSRF)
    await reorderMembers(page, albumPath, originalOrder);
    expect(await memberIds(page, albumPath)).toEqual(originalOrder);
  });

  test('albums_share_modal_without_paired_devices - With no paired devices the Share modal says so', async ({ page }) => {
    const albumPath = await openAlbum(page, SEEDED_ALBUM);
    await page.goto(albumPath);
    await page.locator('#share-album-button').click();
    const modal = page.locator('#shareAlbumModal');
    await expect(modal).toBeVisible();

    // No devices to share with, and it says to pair one first
    await expect(modal.locator('input[name="device_id"]')).toHaveCount(0);
    await expect(modal.locator('.no-data')).toContainText('Pair a device on the Sharing tab first');
    await modal.locator('button[name="cancel"]').first().click();
    await expect(modal).not.toBeVisible();

    // Nothing in this environment is shared
    await page.goto('/albums');
    await expect(page.locator('.album-tile .chip', { hasText: 'Shared' })).toHaveCount(0);
  });
});
