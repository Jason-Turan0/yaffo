import { test, expect, Page, Locator } from '@playwright/test';
import {
  CONTRACT_WIDTHS,
  VIEWPORTS,
  expectFitsViewport,
  expectNoPageOverflow,
  expectPanelContract,
  expectRouteFits,
  withTouchContext,
} from '../_support/responsive';

const UNIQ = Date.now();
const LIST_NAME = `SpecTestList-${UNIQ}`;
const ADD_NAME = `SpecTestPerson-${UNIQ}`;
const EDIT_NAME = `SpecTestEdit-${UNIQ}`;
const RENAMED_NAME = `SpecTestRenamed-${UNIQ}`;
const DELETE_NAME = `SpecTestDelete-${UNIQ}`;
const FACES_NAME = `SpecTestFaces-${UNIQ}`;
// One unbroken run of characters — the long-content case a person's name can
// actually produce, and the one that widens a table if nothing breaks it.
const LONG_NAME = `SpecTestUnbrokenPersonName${'x'.repeat(60)}-${UNIQ}`;
const ALL_TEST_NAMES = [LIST_NAME, ADD_NAME, EDIT_NAME, RENAMED_NAME, DELETE_NAME, FACES_NAME, LONG_NAME];

// Generous per-test budget: the face-assignment waits can sit behind minutes of
// queued model work when the whole suite runs in parallel.
test.describe.configure({ mode: 'serial', timeout: 300_000 });

function flashSuccess(page: Page): Locator {
  return page.locator('.flash-messages .alert-success');
}

function personRow(page: Page, name: string): Locator {
  return page.locator('.people-table tbody tr').filter({ hasText: name });
}

/**
 * Creates a person through the browser's fetch (which automatically includes
 * the CSRF token via security.js). Navigates to /people first to ensure
 * APP_CONFIG.csrfToken is loaded into the page context.
 */
async function createPersonViaApi(page: Page, name: string): Promise<number> {
  await page.goto('/people');

  const result = await page.evaluate(async (personName) => {
    const response = await fetch('/api/people/create', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: personName }),
    });
    const body = await response.json().catch(() => null);
    return { status: response.status, ok: response.ok, body };
  }, name);

  expect(result.ok, `Failed to create person "${name}": HTTP ${result.status} ${JSON.stringify(result.body)}`).toBeTruthy();
  return result.body.person_id as number;
}

/**
 * Deletes a person through the browser's fetch (includes CSRF token via
 * security.js). NOTE: fetch() follows redirects silently, which consumes
 * any server-side flash message. Use this for cleanup only — the
 * people_can_delete_person test body uses a form submission instead so the
 * flash message renders on the navigated page.
 */
async function deletePersonViaApi(page: Page, personId: number): Promise<void> {
  await page.evaluate(async (id) => {
    await fetch(`/people/${id}/delete`, { method: 'POST' });
  }, personId);
}

// Pick an option from the custom searchable-select widget that wraps a native
// <select> (the native element is display:none, so selectOption won't work).
async function pickSearchableOption(scope: Locator, selectSelector: string, optionText: string): Promise<void> {
  const wrapper = scope.locator(`${selectSelector} + .searchable-select-wrapper`);
  await wrapper.locator('.searchable-select-display').click();
  await wrapper.locator('.searchable-select-option').filter({ hasText: optionText }).first().click();
}

// Type into the intl-date-input's visible field and blur so the component parses
// the locale-formatted text into the hidden ISO input the form actually submits.
async function setBirthdate(scope: Locator, visibleInputId: string, localeText: string, expectedIso: string): Promise<void> {
  const visible = scope.locator(`#${visibleInputId}`);
  await visible.fill(localeText);
  await visible.blur();
  await expect(scope.locator('.intl-date-input-control input[name="birthdate"]')).toHaveValue(expectedIso);
}

// The ids of all currently unassigned faces. Grouping by people at the strictest
// threshold puts every face in a rendered group (including DBSCAN noise), making
// this helper independent of the fixture's similarity distribution.
async function unassignedFaceIdsByGroup(page: Page): Promise<number[][]> {
  await page.goto('/faces?group_by=people&threshold=100');
  await expect(page.locator('.main-content')).toBeVisible();
  const groups = page.locator('.suggestion-group');
  const byGroup: number[][] = [];
  for (let i = 0; i < await groups.count(); i += 1) {
    const json = await groups.nth(i).getAttribute('data-faces');
    if (!json) continue;
    byGroup.push((JSON.parse(json) as { id: number }[]).map(face => face.id));
  }
  return byGroup;
}

async function unassignedFaceIds(page: Page): Promise<number[]> {
  return (await unassignedFaceIdsByGroup(page)).flat();
}

async function activeSimilarityClusterFaceIds(page: Page): Promise<number[]> {
  await page.goto('/faces?group_by=similarity&threshold=2');
  const firstGroup = page.locator('.suggestion-group').first();
  if (await firstGroup.count() === 0) return [];
  const json = await firstGroup.getAttribute('data-faces');
  return json ? (JSON.parse(json) as { id: number }[]).map(face => face.id) : [];
}

// The face_assignment suite mutates its active similarity cluster while running
// in parallel. Reserve that cluster dynamically, then take a face from the tail
// of the complete unassigned pool.
async function pickPoolFaceId(page: Page): Promise<number> {
  const reservedFaceIds = new Set(await activeSimilarityClusterFaceIds(page));
  const byGroup = await unassignedFaceIdsByGroup(page);
  const candidates = byGroup.flat().filter(id => !reservedFaceIds.has(id));
  expect(candidates.length, 'expected an unassigned face outside the active similarity cluster').toBeGreaterThan(0);
  return candidates[candidates.length - 1];
}

// Face assignment runs as a background task behind the shared taskq worker;
// parallel suites can queue slow model work ahead of it, so poll generously.
async function waitForFaceAssigned(page: Page, personId: number, faceId: number): Promise<void> {
  await expect(async () => {
    await page.goto(`/people/${personId}/faces`);
    await expect(page.locator(`[data-face-id="${faceId}"]`)).toBeVisible({ timeout: 1000 });
  }).toPass({ timeout: 90_000 });
}

async function waitForFaceBackInPool(page: Page, faceId: number): Promise<void> {
  await expect(async () => {
    const ids = await unassignedFaceIds(page);
    expect(ids).toContain(faceId);
  }).toPass({ timeout: 90_000 });
}

/**
 * Assigns a face to a person through the browser's fetch (includes CSRF token).
 * The server enqueues a background task, so completion is asynchronous —
 * use waitForFaceAssigned() before depending on the result.
 */
async function assignFaceToPersonViaApi(page: Page, faceId: number, personId: number): Promise<void> {
  const result = await page.evaluate(async (params) => {
    const response = await fetch('/api/faces/assign', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        faces: [params.faceId],
        person: params.personId,
        faceStatus: 'ASSIGNED',
      }),
    });
    return { ok: response.ok, status: response.status };
  }, { faceId, personId });
  expect(result.ok, `Failed to assign face ${faceId} to person ${personId}: HTTP ${result.status}`).toBeTruthy();
}

// UI delete used by afterAll cleanup: people list -> Delete -> global confirm.
async function deletePersonByNameUI(page: Page, name: string): Promise<void> {
  await page.goto('/people');
  const row = personRow(page, name);
  if (await row.count() === 0) return;
  await row.locator('[data-action="delete"]').click();
  await page.locator('#confirm-dialog-confirm').click();
  await expect(personRow(page, name)).toHaveCount(0);
}

test.describe('People', () => {
  test.afterAll(async ({ browser }) => {
    const baseURL = process.env.BASE_URL || 'http://127.0.0.1:5001';
    const context = await browser.newContext({ baseURL });
    const page = await context.newPage();
    for (const name of ALL_TEST_NAMES) {
      await deletePersonByNameUI(page, name).catch(() => {});
    }
    await context.close();
  });

  test('people_list_displays_all_people', async ({ page }) => {
    const personId = await createPersonViaApi(page, LIST_NAME);

    await page.goto('/people');
    await expect(page.locator('.page-header')).toContainText('People');
    for (const heading of ['Name', 'Faces', 'Photos', 'Actions']) {
      await expect(page.locator('.people-table th').filter({ hasText: heading })).toBeVisible();
    }

    // A fresh person shows zero faces and zero photos plus Edit/Delete actions.
    const row = personRow(page, LIST_NAME);
    await expect(row.locator('a.person-name')).toHaveText(LIST_NAME);
    await expect(row.locator('.stat-number').nth(0)).toHaveText('0');
    await expect(row.locator('.stat-number').nth(1)).toHaveText('0');
    await expect(row.locator('[data-action="edit"]')).toBeVisible();
    await expect(row.locator('[data-action="delete"]')).toBeVisible();

    // Note: the "No people yet" empty state only renders with zero people in the
    // database; concurrent suites create people at will, so it is not asserted here.

    await deletePersonViaApi(page, personId);
  });

  test('people_can_add_new_person', async ({ page }) => {
    await page.goto('/people');
    await page.locator('.js-add-person').first().click();
    const modal = page.locator('#addModal');
    await expect(modal).toHaveClass(/active/);

    // Name, Birthdate, and Gender fields are present.
    await expect(modal.locator('#addPersonName')).toBeVisible();
    await expect(modal.locator('#addPersonBirthdate')).toBeVisible();
    await expect(modal.locator('select[name="gender"] + .searchable-select-wrapper')).toBeVisible();

    await modal.locator('#addPersonName').fill(ADD_NAME);
    await pickSearchableOption(modal, 'select[name="gender"]', 'Male');
    await setBirthdate(modal, 'addPersonBirthdate', '01/15/1990', '1990-01-15');

    await modal.locator('button[type="submit"]').click();
    // Flash text is locale-dependent (a parallel suite may briefly switch the
    // app language); the interpolated person name is stable in every locale.
    await expect(flashSuccess(page)).toContainText(ADD_NAME);
    await expect(personRow(page, ADD_NAME)).toHaveCount(1);

    // Cleanup: remove the created person.
    const editLink = personRow(page, ADD_NAME).locator('[data-action="edit"]');
    const personId = Number(await editLink.getAttribute('data-person-id'));
    await deletePersonViaApi(page, personId);
  });

  test('people_can_edit_person', async ({ page }) => {
    const personId = await createPersonViaApi(page, EDIT_NAME);

    await page.goto('/people');
    await personRow(page, EDIT_NAME).locator('[data-action="edit"]').click();
    const modal = page.locator('#editModal');
    await expect(modal).toHaveClass(/active/);

    // The edit modal is pre-populated with the person's current details.
    await expect(modal.locator('#editPersonName')).toHaveValue(EDIT_NAME);

    await modal.locator('#editPersonName').fill(RENAMED_NAME);
    await pickSearchableOption(modal, 'select[name="gender"]', 'Female');
    await setBirthdate(modal, 'editPersonBirthdate', '03/20/1985', '1985-03-20');
    await modal.locator('button[type="submit"]').click();

    await expect(flashSuccess(page)).toContainText(RENAMED_NAME);
    await expect(personRow(page, RENAMED_NAME)).toHaveCount(1);
    await expect(personRow(page, EDIT_NAME)).toHaveCount(0);

    // The saved details round-trip: reopening the modal shows them.
    await personRow(page, RENAMED_NAME).locator('[data-action="edit"]').click();
    await expect(modal.locator('#editPersonName')).toHaveValue(RENAMED_NAME);
    await expect(modal.locator('select[name="gender"]')).toHaveValue('0');
    await expect(modal.locator('.intl-date-input-control input[name="birthdate"]')).toHaveValue('1985-03-20');
    await modal.locator('.modal-actions [name="cancel"]').click();

    await deletePersonViaApi(page, personId);
  });

  test('people_can_delete_person', async ({ page }) => {
    const personId = await createPersonViaApi(page, DELETE_NAME);
    const faceId = await pickPoolFaceId(page);
    await assignFaceToPersonViaApi(page, faceId, personId);
    await waitForFaceAssigned(page, personId, faceId);

    await page.goto('/people');

    // Clicking delete shows the confirmation dialog.
    await personRow(page, DELETE_NAME).locator('[data-action="delete"]').click();
    const dialog = page.locator('#global-confirm-dialog');
    await expect(dialog).toHaveClass(/active/);
    await expect(dialog).toContainText(DELETE_NAME);

    // Cancel the dialog, then submit a form with a CSRF token via page.evaluate.
    // people/list.js confirmDelete() creates a dynamic form without a CSRF token,
    // and fetch()-based deletion consumes the server flash message on redirect —
    // so we create and submit a proper form that navigates the page naturally.
    await page.locator('#confirm-dialog-cancel').click();

    await page.evaluate(async (id) => {
      const csrfToken = (window as any).APP_CONFIG.csrfToken;
      const form = document.createElement('form');
      form.method = 'POST';
      form.action = `/people/${id}/delete`;
      const csrfInput = document.createElement('input');
      csrfInput.type = 'hidden';
      csrfInput.name = 'csrf_token';
      csrfInput.value = csrfToken;
      form.appendChild(csrfInput);
      document.body.appendChild(form);
      form.submit();
    }, personId);

    // The form submission redirects to /people with the flash message rendered.
    await page.waitForURL('**/people');
    await expect(flashSuccess(page)).toContainText(DELETE_NAME);
    await expect(personRow(page, DELETE_NAME)).toHaveCount(0);

    // The person's face returns to the unassigned pool.
    await waitForFaceBackInPool(page, faceId);
  });

  test('people_can_view_and_remove_faces', async ({ page }) => {
    const personId = await createPersonViaApi(page, FACES_NAME);
    const faceId = await pickPoolFaceId(page);
    await assignFaceToPersonViaApi(page, faceId, personId);
    await waitForFaceAssigned(page, personId, faceId);

    // The people list reflects the assignment.
    await page.goto('/people');
    await expect(personRow(page, FACES_NAME).locator('.stat-number').nth(0)).toHaveText('1');

    // The person's faces view shows the assigned face thumbnail.
    await personRow(page, FACES_NAME).locator('a.person-name').click();
    const card = page.locator(`.face-card[data-face-id="${faceId}"]`);
    await expect(card).toBeVisible();
    await expect(card.locator('img')).toBeVisible();

    // Select the face and remove it from the person (confirm dialog in between).
    await card.click();
    await expect(card).toHaveClass(/selected/);
    await page.locator('#remove-selected-faces').click();
    await expect(page.locator('#global-confirm-dialog')).toHaveClass(/active/);
    await page.locator('#confirm-dialog-confirm').click();

    // "Person updated" carries no interpolated values, so only the success
    // category is locale-independent; the empty state is asserted structurally.
    await expect(flashSuccess(page)).toBeVisible();
    await expect(page.locator('.empty-state')).toBeVisible();
    await expect(page.locator('.face-card')).toHaveCount(0);

    // The face count decreases and the face is available for assignment again.
    await page.goto('/people');
    await expect(personRow(page, FACES_NAME).locator('.stat-number').nth(0)).toHaveText('0');
    await waitForFaceBackInPool(page, faceId);

    await deletePersonViaApi(page, personId);
  });
});

// ---------------------------------------------------------------------------
// Responsive behaviour of the people family (P3). The shell contract itself is
// exercised on Home; these cases are about this family's own narrow-screen
// behaviour. Contract assertions are imported from _support/responsive.ts.
// ---------------------------------------------------------------------------

/**
 * A person id for the person-faces route. Prefers someone who actually has faces
 * so the gallery, not the empty state, is what gets measured; falls back to the
 * first row when the parallel suites have emptied everyone out.
 */
async function firstPersonId(page: Page): Promise<number> {
  await page.goto('/people');
  const rows = page.locator('.people-table tbody tr');
  await expect(rows.first()).toBeAttached();
  const candidates = await rows.evaluateAll(elements => elements.map((row) => {
    const href = row.querySelector('a.person-name.row-link')?.getAttribute('href') ?? '';
    const faces = Number(row.querySelectorAll('.stat-number')[0]?.textContent?.replace(/\D/g, '') || '0');
    return { id: Number(href.match(/\/people\/(\d+)\/faces/)?.[1] ?? 0), faces };
  }));
  const withFaces = candidates.filter(candidate => candidate.id && candidate.faces > 0);
  const chosen = withFaces.sort((a, b) => b.faces - a.faces)[0] ?? candidates.find(c => c.id);
  expect(chosen, 'expected at least one person on the people list').toBeTruthy();
  return chosen!.id;
}

test.describe('People — responsive', () => {
  test.describe.configure({ timeout: 120_000 });

  test('the people list renders without page-level overflow at every contract width', async ({ page }) => {
    for (const width of CONTRACT_WIDTHS) {
      await page.setViewportSize({ width, height: 900 });
      await expectRouteFits(page, '/people');
    }
    await page.setViewportSize(VIEWPORTS.narrowLandscape);
    await expectRouteFits(page, '/people');
  });

  test('people rows become labelled cards at narrow widths without dropping a column', async ({ page }) => {
    await page.setViewportSize(VIEWPORTS.narrow);
    await page.goto('/people');

    // Every cell still renders, and each one carries its column name so the card
    // reads as a labelled record rather than an unlabelled stack of values.
    const firstRowCells = await page.locator('.people-table tbody tr').first().locator('td').evaluateAll(
      cells => cells.map(cell => ({
        label: cell.getAttribute('data-label'),
        rendered: getComputedStyle(cell, '::before').content,
        display: getComputedStyle(cell).display,
      })),
    );
    expect(firstRowCells).toHaveLength(6);
    for (const cell of firstRowCells) {
      expect(cell.label, 'every cell needs its column name as data-label').toBeTruthy();
      expect(cell.rendered).toContain(cell.label!);
    }

    // No data is hidden to make the row fit: the header is only visually hidden
    // (it is repeated per cell), and no cell is display:none.
    for (const cell of firstRowCells) {
      expect(cell.display).not.toBe('none');
    }
    await expect(page.locator('.people-table tbody tr').first()).toBeVisible();

    // Whatever tabular surface is left contains its own overflow.
    const table = await page.locator('.people-table').evaluate(element => ({
      clientWidth: element.clientWidth,
      scrollWidth: element.scrollWidth,
      overflowX: getComputedStyle(element).overflowX,
    }));
    expect(table.overflowX).toBe('auto');
    expect(table.clientWidth).toBeLessThanOrEqual(page.viewportSize()!.width);
    await expectNoPageOverflow(page);
  });

  test('a long unbroken person name does not widen the people list', async ({ page }) => {
    const personId = await createPersonViaApi(page, LONG_NAME);
    try {
      for (const width of [320, 390, 768, 1440]) {
        await page.setViewportSize({ width, height: 900 });
        await page.goto('/people');
        await expect(personRow(page, LONG_NAME)).toHaveCount(1);
        await expectNoPageOverflow(page);
      }

      // Regression: the same name is interpolated into the person-faces heading
      // AND into that page's empty-state sentence ("No faces have been assigned
      // to <name> yet."). The unbroken run there set the page's minimum width —
      // 672px at a 320px viewport — until both were made breakable.
      await page.setViewportSize(VIEWPORTS.minimum);
      await page.goto(`/people/${personId}/faces`);
      await expect(page.locator('.page-header h1')).toContainText('SpecTestUnbrokenPersonName');
      await expect(page.locator('.empty-state p')).toContainText('SpecTestUnbrokenPersonName');
      await expectNoPageOverflow(page);
    } finally {
      await deletePersonViaApi(page, personId);
    }
  });

  test('the add-person dialog fits a 320px viewport and keeps its body as the only scroll region', async ({ page }) => {
    await page.setViewportSize(VIEWPORTS.minimum);
    await page.goto('/people');
    await page.locator('.js-add-person').first().click();

    const modal = page.locator('#addModal');
    await expect(modal).toHaveClass(/active/);
    await expectFitsViewport(page, '#addModal .modal-content');

    const body = await modal.locator('.modal-body').evaluate(element => ({
      clientWidth: element.clientWidth,
      scrollWidth: element.scrollWidth,
    }));
    expect(body.scrollWidth).toBeLessThanOrEqual(body.clientWidth + 1);

    // Footer actions stack rather than overflowing, and stay full-size targets.
    const actions = await modal.locator('.modal-actions button').evaluateAll(
      buttons => buttons.map(button => button.getBoundingClientRect()),
    );
    for (const box of actions) {
      expect(box.height).toBeGreaterThanOrEqual(40);
      expect(box.right).toBeLessThanOrEqual(320 + 1);
    }
    await expectNoPageOverflow(page);
  });

  test('an in-progress add-person edit survives a resize through the breakpoint', async ({ page }) => {
    await page.setViewportSize(VIEWPORTS.narrow);
    await page.goto('/people');
    await page.locator('.js-add-person').first().click();

    const modal = page.locator('#addModal');
    await expect(modal).toHaveClass(/active/);
    await modal.locator('#addPersonName').fill('HalfTypedPerson');

    await page.setViewportSize(VIEWPORTS.desktop);
    // Nothing reloads, so the dialog and the half-typed name are both still there.
    await expect(modal).toHaveClass(/active/);
    await expect(modal.locator('#addPersonName')).toHaveValue('HalfTypedPerson');
    await expectFitsViewport(page, '#addModal .modal-content');

    await modal.locator('.modal-actions [name="cancel"]').click();
  });

  test('the person faces route renders without page-level overflow at every contract width', async ({ page }) => {
    const personId = await firstPersonId(page);
    for (const width of CONTRACT_WIDTHS) {
      await page.setViewportSize({ width, height: 900 });
      await expectRouteFits(page, `/people/${personId}/faces`);
    }
  });

  test('the person faces Actions panel satisfies the peer-panel contract', async ({ page }) => {
    const personId = await firstPersonId(page);
    await expectPanelContract(page, {
      route: `/people/${personId}/faces`,
      panelId: 'person-faces-actions',
    });
  });

  test('the person faces Filters panel satisfies the peer-panel contract', async ({ page }) => {
    const personId = await firstPersonId(page);
    await expectPanelContract(page, {
      route: `/people/${personId}/faces`,
      panelId: 'person-faces-filters',
    });
  });

  test('person faces declares Actions and Filters as peers of Menu, Actions first', async ({ page }) => {
    const personId = await firstPersonId(page);
    await page.setViewportSize(VIEWPORTS.narrow);
    await page.goto(`/people/${personId}/faces`);

    const toggleIds = await page.locator('[data-nav-panel-toggle], #nav-menu-toggle').evaluateAll(
      elements => elements.map(element => element.id),
    );
    expect(toggleIds).toEqual([
      'person-faces-actions-toggle',
      'person-faces-filters-toggle',
      'nav-menu-toggle',
    ]);

    // A filter value entered on narrow survives the trip back to desktop.
    await page.locator('#person-faces-filters-toggle').click();
    await page.locator('#person-faces-filters input[name="min_similarity"]').first().fill('42');
    await page.setViewportSize(VIEWPORTS.desktop);
    await expect(page.locator('#person-faces-filters input[name="min_similarity"]').first()).toHaveValue('42');
    await expectNoPageOverflow(page);
  });

  test('the person face gallery keeps its selection controls usable with a coarse pointer', async ({ browser }) => {
    const setup = await browser.newContext({ baseURL: process.env.BASE_URL || 'http://127.0.0.1:5001' });
    const setupPage = await setup.newPage();
    const personId = await firstPersonId(setupPage);
    await setup.close();

    await withTouchContext(browser, VIEWPORTS.narrow, async (page) => {
      await page.goto(`/people/${personId}/faces`);
      await expectNoPageOverflow(page);

      const cards = page.locator('.face-card');
      if (await cards.count() === 0) {
        // No assigned faces for this person: the empty state is the whole page,
        // and there is nothing further to exercise here.
        await expect(page.locator('.empty-state')).toBeVisible();
        return;
      }

      // Select all / Clear selection are real touch targets, not 16px links.
      for (const id of ['select-all', 'deselect-all']) {
        const box = await page.locator(`#${id}`).boundingBox();
        expect(box!.height, `#${id} is too small to tap`).toBeGreaterThanOrEqual(44);
      }

      // Tapping a card selects it — the card hover lift is not the affordance.
      const card = cards.first();
      await card.tap();
      await expect(card).toHaveClass(/selected/);
      await page.locator('#deselect-all').tap();
      await expect(card).not.toHaveClass(/selected/);
      await expectNoPageOverflow(page);
    });
  });
});
