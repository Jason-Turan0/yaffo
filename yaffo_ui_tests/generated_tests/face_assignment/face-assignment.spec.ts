import { test, expect, Page } from '@playwright/test';
import {
  CONTRACT_WIDTHS,
  VIEWPORTS,
  expectFitsViewport,
  expectNoPageOverflow,
  expectPanelContract,
  expectRouteFits,
  touchDrag,
  withTouchContext,
} from '../_support/responsive';

// Type definition for person data
type PersonInfo = {
  id: number;
  name: string;
};

// These tests share one server-side pool of unassigned faces and create/delete
// people, so they must not interleave.
test.describe.configure({ mode: 'serial', timeout: 30_000 });

/**
 * Deletes a person by POSTing to the server via the browser's fetch (which
 * automatically includes the CSRF token via security.js), then reloading the
 * people list to verify. The UI's confirm-dialog path creates a form without a
 * CSRF token, so we bypass it entirely.
 * @param page The Playwright Page object.
 * @param personName The name of the person to delete.
 */
async function deletePersonByName(page: Page, personName: string): Promise<void> {
  await page.goto('/people');
  const personRow = page.locator('tr').filter({ hasText: personName });
  if (await personRow.count() === 0) return;

  // Extract the person ID from the edit link's data attribute
  const editLink = personRow.locator('[data-action="edit"]');
  const personId = await editLink.getAttribute('data-person-id');
  expect(personId, `Expected to find data-person-id for "${personName}"`).not.toBeNull();

  // POST the delete through the browser's fetch so security.js adds the CSRF
  // token. fetch follows the redirect silently — the person is deleted on the
  // server but the page still shows stale content, so we reload afterward.
  await page.evaluate(async (id) => {
    await fetch(`/people/${id}/delete`, { method: 'POST' });
  }, Number(personId));

  // Reload the people list; the row should be gone.
  await page.goto('/people');
  await expect(personRow).toHaveCount(0);
}

/**
 * Creates a person through the browser's fetch (which automatically includes
 * the CSRF token via security.js). Falls back to looking up an existing person
 * by navigating to the people list when the API reports a duplicate.
 * @param page The Playwright Page object.
 * @param personName The name of the person to create.
 * @returns The created person's information (id and name).
 */
async function createPersonViaApi(page: Page, personName: string): Promise<PersonInfo> {
    // Use page.evaluate so the fetch goes through the browser's security.js
    // interceptor, which automatically adds the X-CSRF-Token header.
    const result = await page.evaluate(async (name) => {
        const response = await fetch('/api/people/create', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name }),
        });
        const body = await response.json().catch(() => null);
        return { status: response.status, ok: response.ok, body };
    }, personName);

    if (result.status === 400) {
        // If person exists, find them on the people page to get their ID
        await page.goto('/people');
        const personRow = page.locator('tr').filter({ hasText: personName });
        await expect(personRow, `Expected to find existing person "${personName}" on the people list`).toHaveCount(1);
        const personLink = personRow.locator('a.person-name.row-link');
        const href = await personLink.getAttribute('href');
        const personId = parseInt(href!.match(/\/people\/(\d+)\/faces/)![1], 10);
        return { id: personId, name: personName };
    }

    expect(result.ok, `Failed to create person "${personName}": HTTP ${result.status} ${JSON.stringify(result.body)}`).toBeTruthy();
    return { id: result.body.person_id, name: result.body.name };
}

/**
 * Assigns a face to a person through the browser's fetch (includes CSRF token).
 * The server enqueues a background task, so completion is asynchronous —
 * use waitForFaceAssigned() before depending on the result.
 * @param page The Playwright Page object.
 * @param faceId The ID of the face to assign.
 * @param personId The ID of the person to assign the face to.
 */
async function assignFaceToPersonViaApi(page: Page, faceId: number, personId: number): Promise<void> {
    const result = await page.evaluate(async (params) => {
        const response = await fetch('/api/faces/assign', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                faces: [params.faceId],
                person: params.personId,
                faceStatus: 'ASSIGNED'
            }),
        });
        return { ok: response.ok, status: response.status };
    }, { faceId, personId });
    expect(result.ok, `Failed to assign face ${faceId} to person ${personId}: HTTP ${result.status}`).toBeTruthy();
}

/**
 * Polls the person's faces page until the background assignment task has
 * completed and the face shows up there.
 */
async function waitForFaceAssigned(page: Page, personId: number, faceId: string | number): Promise<void> {
  await expect(async () => {
    await page.goto(`/people/${personId}/faces`);
    await expect(page.locator(`[data-face-id="${faceId}"]`)).toBeVisible({ timeout: 1000 });
  }).toPass({ timeout: 15000 });
}

/**
 * Picks an option from the custom searchable-select widget that wraps a
 * native <select> (the native element is hidden, so selectOption won't work).
 */
async function pickSearchableOption(page: Page, selectSelector: string, optionText: string): Promise<void> {
  const wrapper = page.locator(`${selectSelector} + .searchable-select-wrapper`);
  await wrapper.locator('.searchable-select-display').click();
  await wrapper.locator('.searchable-select-option').filter({ hasText: optionText }).first().click();
}

test.describe('Face Assignment', () => {
  // Clean up before each test so the database is in a known state regardless
  // of what a previous (possibly broken) run left behind.
  test.beforeEach(async ({ page }) => {
    await deletePersonByName(page, 'Obama');
    await deletePersonByName(page, 'TestKeyboardPerson');
  });

  // Cleanup after each test to restore the shared face pool for the next test.
  test.afterEach(async ({ page }) => {
    await deletePersonByName(page, 'Obama');
    await deletePersonByName(page, 'TestKeyboardPerson');
  });

  test('should be able to create a new person using the quick action section', async ({ page }) => {
    await page.goto('/faces');

    // Type in the name 'Obama' and click Create Person
    await page.locator('#create-person-name').fill('Obama');
    const [response] = await Promise.all([
      page.waitForResponse(resp => resp.url().includes('/api/people/create')),
      page.locator('#create-person-btn').click(),
    ]);
    expect(response.status()).toBe(201);

    // A success toast is shown, then the page reloads (~1.5s later) and the
    // person select in the sidebar is repopulated with the new person.
    // (The toast itself is racy to assert — the reload wipes it — so verify
    // the durable outcome instead.)
    const personOption = page.locator('#sidebar-person-select option').filter({ hasText: 'Obama' });
    await expect(personOption).toHaveCount(1, { timeout: 10000 });
  });

  test('should be able to assign faces to people', async ({ page }) => {
    const obama = await createPersonViaApi(page, 'Obama');

    // Low threshold so the unassigned faces cluster into visible groups
    await page.goto('/faces?group_by=similarity&threshold=2');

    // Only the first suggestion group is shown; its faces start selected.
    const firstGroup = page.locator('.suggestion-group').first();
    await expect(firstGroup).toBeVisible();
    const firstFace = firstGroup.locator('.face').first();
    await expect(firstFace).toBeVisible();

    // Deselect the whole cluster, then select a single face. The select-all control
    // is a chip whose label names its next action, so with everything selected it
    // reads "Clear selection".
    await firstGroup.locator('.cluster-select-all').click();
    await expect(firstFace).not.toHaveClass(/selected/);
    await firstFace.click();
    await expect(firstFace).toHaveClass(/selected/);
    const faceId = await firstFace.getAttribute('data-face-id');
    expect(faceId).not.toBeNull();

    // Pick Obama in the sidebar's searchable person select and assign
    await pickSearchableOption(page, '#sidebar-person-select', 'Obama');
    const [response] = await Promise.all([
      page.waitForResponse(resp => resp.url().includes('/api/faces/assign')),
      page.locator('#sidebar-assign-selected-btn').click(),
    ]);
    expect(response.ok()).toBeTruthy();
    await expect(page.locator('.notification.visible')).toBeVisible();

    // Assignment completes in a background task; the face lands on Obama's page
    await waitForFaceAssigned(page, obama.id, faceId!);
  });

  test('faces are automatically matched to people based on similarity', async ({ page }) => {
    // Establish a baseline from the live unassigned pool. Face ids are generated
    // by indexing and therefore aren't stable across fixture changes.
    const obama = await createPersonViaApi(page, 'Obama');
    await page.goto('/faces?group_by=similarity&threshold=2');
    const seedGroup = page.locator('.suggestion-group').first();
    await expect(seedGroup).toBeVisible();
    const seedFaces = JSON.parse(await seedGroup.getAttribute('data-faces') ?? '[]') as { id: number }[];
    expect(seedFaces.length).toBeGreaterThanOrEqual(3);
    const baselineFaceId = seedFaces[0].id;
    await assignFaceToPersonViaApi(page, baselineFaceId, obama.id);
    await waitForFaceAssigned(page, obama.id, baselineFaceId);

    // Group by people with a low similarity threshold to match generously
    await page.goto('/faces?group_by=people&threshold=2');

    // A suggestion group for 'Obama' exists (it may not be the active/visible
    // one, so assert on the DOM rather than visibility)
    const obamaGroup = page.locator('.suggestion-group').filter({
      has: page.locator('.cluster-name', { hasText: 'Obama' }),
    }).first();
    await expect(obamaGroup).toBeAttached();

    // Every face matched to Obama carries a positive similarity score
    const obamaFaces = JSON.parse(await obamaGroup.getAttribute('data-faces') ?? '[]');
    expect(obamaFaces.length).toBeGreaterThan(0);
    for (const face of obamaFaces) {
      expect(face.similarity).toBeGreaterThan(0);
    }

    // The group offers a one-click "Assign to Obama" action
    await expect(obamaGroup.locator('.assign-group-btn[data-person-name="Obama"]')).toBeAttached();

    // Faces in the first (active) group are selected by default
    const firstGroupFaces = page.locator('.suggestion-group').first().locator('.face');
    await expect(firstGroupFaces.first()).toHaveClass(/selected/);
  });

  test('similar faces are grouped together', async ({ page }) => {
    // Cluster unknown faces by similarity with a loose threshold
    await page.goto('/faces?group_by=similarity&threshold=2');

    const groups = page.locator('.suggestion-group');
    await expect(groups.first()).toBeVisible();

    // Verify all groups meet the clustering minimum size (DBSCAN min_samples=3).
    // Faces render lazily into the active group only, so check the data payload.
    for (const group of await groups.all()) {
      const faces = JSON.parse(await group.getAttribute('data-faces') ?? '[]');
      expect(faces.length).toBeGreaterThanOrEqual(3);
    }

    // The first group is active with everything selected for quick assignment — so
    // the select-all chip offers to CLEAR it.
    await expect(groups.first().locator('.cluster-select-all')).toHaveText(/Clear selection/);
    await expect(groups.first().locator('.face.selected').first()).toBeVisible();

    // Only one cluster is worked on at a time; the rest stay hidden
    if (await groups.count() > 1) {
      await expect(groups.nth(1)).toBeHidden();
    }
  });

  test('keyboard shortcuts enable quick face assignment', async ({ page }) => {
    const person = await createPersonViaApi(page, 'TestKeyboardPerson');
    await page.goto('/faces?group_by=similarity&threshold=2');

    // Note the keyboard shortcut number displayed for the person in the sidebar
    const shortcutItem = page.locator('#sidebar-shortcut-people .shortcut-item').filter({ hasText: 'TestKeyboardPerson' });
    await expect(shortcutItem).toBeVisible();
    const shortcutKey = await shortcutItem.locator('kbd').textContent();
    expect(shortcutKey).toBeTruthy();

    // Get the IDs of the faces that are selected by default (in the first group)
    const firstGroup = page.locator('.suggestion-group').first();
    const selectedFaceLocators = firstGroup.locator('.face.selected');
    await expect(selectedFaceLocators.first()).toBeVisible();
    const faceIds = await selectedFaceLocators.evaluateAll(elements =>
        elements.map(el => el.getAttribute('data-face-id'))
    );
    expect(faceIds.length).toBeGreaterThan(0);

    // Press the shortcut key to assign the whole selected cluster
    const [response] = await Promise.all([
      page.waitForResponse(resp => resp.url().includes('/api/faces/assign')),
      page.keyboard.press(shortcutKey!),
    ]);
    expect(response.ok()).toBeTruthy();

    // Assigning advances past the cluster (or reloads for the next batch), so
    // the assigned faces leave the view
    for (const faceId of faceIds) {
      await expect(page.locator(`.face[data-face-id="${faceId}"]`)).not.toBeVisible();
    }

    // Wait for the background task to finish so afterEach cleanup can't race it
    await waitForFaceAssigned(page, person.id, faceIds[0]!);
  });

  test('face page navigation works', async ({ page }) => {
    await page.goto('/faces?group_by=similarity&threshold=2');

    // The header reports the size of the unassigned pile
    await expect(page.locator('.subtitle')).toContainText(/Showing [\d,.]+ of [\d,.]+ unassigned/);

    const firstGroup = page.locator('.suggestion-group').first();
    await expect(firstGroup).toBeVisible();
    const clusterFaces = JSON.parse(await firstGroup.getAttribute('data-faces') ?? '[]');

    // The cluster pager starts on page 1: First/Previous are disabled
    await expect(firstGroup.locator('.cluster-first')).toBeDisabled();
    await expect(firstGroup.locator('.cluster-prev')).toBeDisabled();

    // Only a sample of up to 50 faces is painted per pager page
    const facesOnPage1 = await firstGroup.locator('.face').count();
    expect(facesOnPage1).toBeGreaterThan(0);
    expect(facesOnPage1).toBeLessThanOrEqual(50);

    if (clusterFaces.length > 50) {
      // Paging forward shows the next sample and enables Previous
      await firstGroup.locator('.cluster-next').click();
      await expect(firstGroup.locator('.sample-range')).toContainText('51');
      await expect(firstGroup.locator('.cluster-prev')).toBeEnabled();

      // Back to the first page
      await firstGroup.locator('.cluster-first').click();
      await expect(firstGroup.locator('.cluster-first')).toBeDisabled();
    } else {
      // Single page: forward navigation is disabled
      await expect(firstGroup.locator('.cluster-next')).toBeDisabled();
      await expect(firstGroup.locator('.cluster-last')).toBeDisabled();
    }

    // Skipping a cluster advances to the next one
    const groups = page.locator('.suggestion-group');
    if (await groups.count() > 1) {
      await firstGroup.locator('.skip-cluster-btn').click();
      await expect(firstGroup).toBeHidden();
      await expect(groups.nth(1)).toBeVisible();
    }
  });
});

// ---------------------------------------------------------------------------
// Responsive behaviour of the faces family (P3). The shell contract itself is
// exercised on Home; what is asserted here is this page's own narrow-screen
// behaviour. Contract assertions come from _support/responsive.ts — a page that
// re-implements an overflow or panel check has forked the contract.
//
// These cases only read the face pool (they never assign or ignore), so they do
// not need the create/delete hooks the workflow tests above rely on.
// ---------------------------------------------------------------------------

// A clustered view, so the grid, the cluster header and the cluster pager are all
// actually on the page when the width is squeezed.
const CLUSTERED_FACES_URL = '/faces?group_by=similarity&threshold=2';

test.describe('Face Assignment — responsive', () => {
  test.describe.configure({ timeout: 90_000 });

  test('faces route renders without page-level overflow at every contract width', async ({ page }) => {
    for (const width of CONTRACT_WIDTHS) {
      await page.setViewportSize({ width, height: 900 });
      await expectRouteFits(page, CLUSTERED_FACES_URL);
    }
    // Short landscape is the other stress case in the support contract: the
    // cluster header, actions and pager all compete for vertical space there.
    await page.setViewportSize(VIEWPORTS.narrowLandscape);
    await expectRouteFits(page, CLUSTERED_FACES_URL);
  });

  test('faces exposes Actions and Filters as separate peers of Menu, Actions first', async ({ page }) => {
    await page.setViewportSize(VIEWPORTS.narrow);
    await page.goto(CLUSTERED_FACES_URL);

    // Two page toggles, declared Actions-then-Filters, and Menu sorts last.
    const toggleIds = await page.locator('[data-nav-panel-toggle], #nav-menu-toggle').evaluateAll(
      elements => elements.map(element => element.id),
    );
    expect(toggleIds).toEqual(['faces-actions-toggle', 'faces-filters-toggle', 'nav-menu-toggle']);

    // The applied-filter count is server-rendered, so it is already correct here
    // rather than popping in after hydration. group_by + threshold = two filters.
    await expect(page.locator('#faces-filters-toggle [data-nav-panel-count]')).toHaveText('2');

    // Opening one page panel closes the other — only one surface at a time.
    await page.locator('#faces-actions-toggle').click();
    await expect(page.locator('#faces-actions')).toBeVisible();
    await page.locator('#faces-filters-toggle').click();
    await expect(page.locator('#faces-filters')).toBeVisible();
    await expect(page.locator('#faces-actions')).toBeHidden();
    await expect(page.locator('#faces-actions-toggle')).toHaveAttribute('aria-expanded', 'false');
    await expectNoPageOverflow(page);
  });

  test('Actions, Filters and Menu fit the top navbar at an iPhone SE width', async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 667 });
    await page.goto(CLUSTERED_FACES_URL);

    const navbar = page.locator('.navbar-container').first();
    const toggles = page.locator(
      '#faces-actions-toggle, #faces-filters-toggle, #nav-menu-toggle',
    );
    await expect(toggles).toHaveCount(3);

    const geometry = await toggles.evaluateAll(elements => elements.map(element => {
      const rect = element.getBoundingClientRect();
      return {
        top: Math.round(rect.top),
        left: rect.left,
        right: rect.right,
        width: rect.width,
        height: rect.height,
      };
    }));
    expect(new Set(geometry.map(rect => rect.top)).size, 'all three controls should share one row').toBe(1);

    const navbarBox = await navbar.boundingBox();
    expect(navbarBox).not.toBeNull();
    for (const rect of geometry) {
      expect(rect.width).toBeGreaterThanOrEqual(44);
      expect(rect.height).toBeGreaterThanOrEqual(44);
      expect(rect.left).toBeGreaterThanOrEqual(navbarBox!.x - 1);
      expect(rect.right).toBeLessThanOrEqual(navbarBox!.x + navbarBox!.width + 1);
    }

    await expect(page.locator('#faces-actions-toggle')).toHaveAttribute('aria-label', 'Actions');
    await expect(page.locator('#faces-filters-toggle')).toHaveAttribute('aria-label', 'Filters');
    await expect(page.locator('#nav-menu-toggle')).toHaveAttribute('aria-label', 'Menu');
    const contextLabels = page.locator('.nav-context-toggle-label');
    await expect(contextLabels).toHaveCount(2);
    await expect(contextLabels.nth(0)).toBeHidden();
    await expect(contextLabels.nth(1)).toBeHidden();
    await expect(page.locator('.nav-menu-toggle-label')).toBeHidden();
    await expectNoPageOverflow(page);
  });

  test('the faces Actions panel satisfies the peer-panel contract', async ({ page }) => {
    await expectPanelContract(page, {
      route: CLUSTERED_FACES_URL,
      panelId: 'faces-actions',
    });
  });

  test('the faces Filters panel satisfies the peer-panel contract', async ({ page }) => {
    await expectPanelContract(page, {
      route: CLUSTERED_FACES_URL,
      panelId: 'faces-filters',
    });
  });

  test('in-progress filter and assignment input survives a resize through the breakpoint', async ({ page }) => {
    await page.setViewportSize(VIEWPORTS.narrow);
    await page.goto(CLUSTERED_FACES_URL);

    // A filter value entered in the narrow panel…
    await page.locator('#faces-filters-toggle').click();
    await page.locator('#threshold-range').fill('37');
    await expect(page.locator('#threshold-value')).toHaveText('37');

    // …and an in-progress edit in the Actions panel.
    await page.locator('#faces-actions-toggle').click();
    await page.locator('#create-person-name').fill('HalfTypedName');

    // Escape closes the open panel (it is the topmost surface here).
    await page.keyboard.press('Escape');
    await expect(page.locator('#faces-actions-toggle')).toHaveAttribute('aria-expanded', 'false');

    // Resizing to desktop moves the live DOM back into the page. Nothing reloads,
    // so both values are still there.
    await page.setViewportSize(VIEWPORTS.desktop);
    await expect(page.locator('#faces-actions-toggle')).toBeHidden();
    await expect(page.locator('#faces-filters-toggle')).toBeHidden();
    await expect(page.locator('#threshold-range')).toHaveValue('37');
    await expect(page.locator('#create-person-name')).toHaveValue('HalfTypedName');
    await expectNoPageOverflow(page);
  });

  test('Group by radio options line up with their own labels', async ({ page }) => {
    await page.setViewportSize(VIEWPORTS.narrow);
    await page.goto(CLUSTERED_FACES_URL);
    await page.locator('#faces-filters-toggle').click();

    for (const id of ['group-by-people', 'group-by-similarity']) {
      const layout = await page.locator(`#${id}`).evaluate((input) => {
        const label = input.closest('label') as HTMLElement;
        const labelStyle = getComputedStyle(label);
        const inputStyle = getComputedStyle(input);
        return {
          display: labelStyle.display,
          alignItems: labelStyle.alignItems,
          gap: inputStyle.marginInlineEnd,
        };
      });
      expect(layout.display).toBe('flex');
      expect(layout.alignItems).toBe('center');
      expect(layout.gap).toBe('8px');
    }
  });

  test('the cluster pager keeps all five controls on one row at 320px', async ({ page }) => {
    // Regression: the cluster pager used to render five full-text buttons
    // ("« First", "‹ Previous", …). At 320px they could not fit on one row, so
    // the footer widened the page. It now reuses the shared pagination markup —
    // data-icon plus a .page-btn-label — which collapses to 44px icons at 640px.
    await page.setViewportSize(VIEWPORTS.minimum);
    await page.goto(CLUSTERED_FACES_URL);

    const navigation = page.locator('.cluster-pager-footer .page-navigation').first();
    await expect(navigation).toBeVisible();

    const rows = await navigation.locator('.page-btn').evaluateAll(
      buttons => Array.from(new Set(buttons.map(button => Math.round(button.getBoundingClientRect().top)))),
    );
    expect(rows, 'the five pager controls should share one row').toHaveLength(1);

    // The text label is dropped in favour of the icon, and the target stays 44px.
    await expect(navigation.locator('.cluster-first .page-btn-label')).toBeHidden();
    const box = await navigation.locator('.cluster-first').boundingBox();
    expect(box!.width).toBeGreaterThanOrEqual(44);
    expect(box!.height).toBeGreaterThanOrEqual(44);
    await expectNoPageOverflow(page);
  });

  test('the phone face source preview stays centered in the viewport', async ({ browser }) => {
    await withTouchContext(browser, VIEWPORTS.narrow, async (page) => {
      await page.goto(CLUSTERED_FACES_URL);
      const visibleFaces = page.locator('.suggestion-group:not([hidden]) .face');
      const face = visibleFaces.first();
      await expect(face).toBeVisible();

      // Hover is unavailable here, so the preview has its own explicit control.
      const previewButton = face.locator('.face-preview-button');
      await expect(previewButton).toBeVisible();
      const buttonBox = await previewButton.boundingBox();
      expect(buttonBox!.width).toBeGreaterThanOrEqual(44);
      expect(buttonBox!.height).toBeGreaterThanOrEqual(44);

      const selectedBefore = await face.evaluate(element => element.classList.contains('selected'));

      const modal = page.locator('.face-preview-modal');
      await previewButton.tap();
      await expect(modal).toHaveClass(/active/);
      await expect(modal).toHaveAttribute('role', 'dialog');
      await expect(modal).toHaveAttribute('aria-modal', 'true');
      await expect(modal.locator('.face-preview-modal-close')).toBeFocused();
      // Previewing and selecting stay distinct actions.
      expect(await face.evaluate(element => element.classList.contains('selected'))).toBe(selectedBefore);
      await expectFitsViewport(page, '.face-preview-modal .modal-content');
      await expect.poll(async () => modal.locator('.modal-content').evaluate(element => {
        const rect = element.getBoundingClientRect();
        return {
          x: Math.round(rect.left + rect.width / 2 - window.innerWidth / 2),
          y: Math.round(rect.top + rect.height / 2 - window.innerHeight / 2),
          overlayPosition: getComputedStyle(element.parentElement!).position,
        };
      })).toEqual({ x: 0, y: 0, overlayPosition: 'fixed' });
      await expectNoPageOverflow(page);

      await modal.locator('.face-preview-modal-close').tap();
      await expect(modal).not.toHaveClass(/active/);
      await expect(previewButton).toBeFocused();

      // Regression: fixed dialogs must remain centered after the trigger has
      // moved the document to a different scroll position.
      const lastPreviewButton = visibleFaces.last().locator('.face-preview-button');
      await lastPreviewButton.scrollIntoViewIfNeeded();
      const scrollY = await page.evaluate(() => window.scrollY);
      expect(scrollY, 'the second preview should open from a scrolled viewport').toBeGreaterThan(0);
      await lastPreviewButton.tap();
      await expect(modal).toHaveClass(/active/);
      await expect.poll(async () => modal.locator('.modal-content').evaluate(element => {
        const rect = element.getBoundingClientRect();
        return {
          x: Math.round(rect.left + rect.width / 2 - window.innerWidth / 2),
          y: Math.round(rect.top + rect.height / 2 - window.innerHeight / 2),
        };
      })).toEqual({ x: 0, y: 0 });
    });
  });

  test('the tablet face source preview opens as an anchored popover', async ({ browser }) => {
    await withTouchContext(browser, VIEWPORTS.tabletPortrait, async (page) => {
      await page.goto(CLUSTERED_FACES_URL);
      const face = page.locator('.suggestion-group:not([hidden]) .face').first();
      const previewButton = face.locator('.face-preview-button');
      await expect(previewButton).toBeVisible();
      const selectedBefore = await face.evaluate(element => element.classList.contains('selected'));

      await previewButton.tap();
      const popover = page.locator('.face-tooltip');
      await expect(popover).toHaveClass(/visible/);
      await expect(page.locator('.face-preview-modal')).not.toHaveClass(/active/);
      expect(await face.evaluate(element => element.classList.contains('selected'))).toBe(selectedBefore);
      await expectFitsViewport(page, '.face-tooltip');
    });
  });

  test('the desktop face source preview opens as a hover popover', async ({ page }) => {
    await page.setViewportSize(VIEWPORTS.desktop);
    await page.goto(CLUSTERED_FACES_URL);
    const face = page.locator('.suggestion-group:not([hidden]) .face').first();
    await face.hover();

    await expect(page.locator('.face-tooltip')).toHaveClass(/visible/);
    await expect(page.locator('.face-preview-modal')).not.toHaveClass(/active/);
    await expectFitsViewport(page, '.face-tooltip');
  });

  test('shortcut people can be reordered with the explicit move controls', async ({ page }) => {
    await page.setViewportSize(VIEWPORTS.narrow);
    await page.goto(CLUSTERED_FACES_URL);
    await page.locator('#faces-actions-toggle').click();
    await page.locator('#configure-shortcuts-btn').click();

    const modal = page.locator('#shortcutPeopleModal');
    await expect(modal).toHaveClass(/active/);
    await expectFitsViewport(page, '#shortcutPeopleModal .modal-content');

    const rows = modal.locator('.shortcut-config-row');
    expect(await rows.count(), 'need two shortcut rows to reorder').toBeGreaterThan(1);
    const before = await rows.evaluateAll(elements => elements.map(element => element.getAttribute('data-person-id')));

    // The second row moves ahead of the first with one tap — no drag involved.
    await rows.nth(1).locator('.shortcut-config-move-btn[data-move="up"]').click();
    const after = await rows.evaluateAll(elements => elements.map(element => element.getAttribute('data-person-id')));
    expect(after[0]).toBe(before[1]);
    expect(after[1]).toBe(before[0]);

    // The ends of the list say so rather than silently doing nothing.
    await expect(rows.first().locator('.shortcut-config-move-btn[data-move="up"]')).toBeDisabled();
    await expect(rows.last().locator('.shortcut-config-move-btn[data-move="down"]')).toBeDisabled();
  });

  test('shortcut people can be reordered with a real touch drag', async ({ browser }) => {
    // Regression: the rows used HTML5 drag-and-drop, which never fires for touch
    // at all — the handle was decorative on a phone. Reordering is Pointer Events
    // now, with the pointer captured on the list rather than on the moving row.
    await withTouchContext(browser, VIEWPORTS.narrow, async (page, context) => {
      await page.goto(CLUSTERED_FACES_URL);
      await page.locator('#faces-actions-toggle').tap();
      await page.locator('#configure-shortcuts-btn').tap();
      await expect(page.locator('#shortcutPeopleModal')).toHaveClass(/active/);

      const rows = page.locator('#shortcut-config-list .shortcut-config-row');
      expect(await rows.count(), 'need two shortcut rows to reorder').toBeGreaterThan(1);
      const before = await rows.evaluateAll(elements => elements.map(element => element.getAttribute('data-person-id')));

      const handle = await rows.first().locator('.filter-config-handle').boundingBox();
      const second = await rows.nth(1).boundingBox();
      await touchDrag(
        context,
        page,
        { x: handle!.x + handle!.width / 2, y: handle!.y + handle!.height / 2 },
        { x: handle!.x + handle!.width / 2, y: second!.y + second!.height * 0.9 },
      );

      const after = await rows.evaluateAll(elements => elements.map(element => element.getAttribute('data-person-id')));
      expect(after[0], 'the dragged row should no longer be first').toBe(before[1]);
      expect(after[1]).toBe(before[0]);
    });
  });

  test('the help dialog contains its own scrolling and fits a 320px viewport', async ({ page }) => {
    await page.setViewportSize(VIEWPORTS.minimum);
    await page.goto(CLUSTERED_FACES_URL);
    await page.keyboard.press('?');

    const modal = page.locator('#keyboardHelpModal');
    await expect(modal).toHaveClass(/active/);
    await expectFitsViewport(page, '#keyboardHelpModal .modal-content');

    // The dialog body is the only scroll region: it never scrolls sideways, and
    // it does not hand its overflow to the document.
    const body = await modal.locator('.modal-body').evaluate(element => ({
      clientWidth: element.clientWidth,
      scrollWidth: element.scrollWidth,
    }));
    expect(body.scrollWidth).toBeLessThanOrEqual(body.clientWidth + 1);
    await expectNoPageOverflow(page);
  });
});
