import { test, expect, Page, Locator, APIRequestContext } from '@playwright/test';

const UNIQ = Date.now();
const LIST_NAME = `SpecTestList-${UNIQ}`;
const ADD_NAME = `SpecTestPerson-${UNIQ}`;
const EDIT_NAME = `SpecTestEdit-${UNIQ}`;
const RENAMED_NAME = `SpecTestRenamed-${UNIQ}`;
const DELETE_NAME = `SpecTestDelete-${UNIQ}`;
const FACES_NAME = `SpecTestFaces-${UNIQ}`;
const ALL_TEST_NAMES = [LIST_NAME, ADD_NAME, EDIT_NAME, RENAMED_NAME, DELETE_NAME, FACES_NAME];

// Generous per-test budget: the face-assignment waits can sit behind minutes of
// queued model work when the whole suite runs in parallel.
test.describe.configure({ mode: 'serial', timeout: 300_000 });

function flashSuccess(page: Page): Locator {
  return page.locator('.flash-messages .alert-success');
}

function personRow(page: Page, name: string): Locator {
  return page.locator('.people-table tbody tr').filter({ hasText: name });
}

async function createPersonViaApi(request: APIRequestContext, name: string): Promise<number> {
  const response = await request.post('/api/people/create', { data: { name } });
  expect(response.status()).toBe(201);
  return (await response.json()).person_id as number;
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

async function assignFaceToPersonViaApi(request: APIRequestContext, faceId: number, personId: number): Promise<void> {
  const response = await request.post('/api/faces/assign', {
    data: { faces: [faceId], person: personId, faceStatus: 'ASSIGNED' },
  });
  expect(response.ok()).toBeTruthy();
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

  test('people_list_displays_all_people', async ({ page, request }) => {
    const personId = await createPersonViaApi(request, LIST_NAME);

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

    await request.post(`/people/${personId}/delete`);
  });

  test('people_can_add_new_person', async ({ page, request }) => {
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
    await request.post(`/people/${personId}/delete`);
  });

  test('people_can_edit_person', async ({ page, request }) => {
    const personId = await createPersonViaApi(request, EDIT_NAME);

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

    await request.post(`/people/${personId}/delete`);
  });

  test('people_can_delete_person', async ({ page, request }) => {
    const personId = await createPersonViaApi(request, DELETE_NAME);
    const faceId = await pickPoolFaceId(page);
    await assignFaceToPersonViaApi(request, faceId, personId);
    await waitForFaceAssigned(page, personId, faceId);

    await page.goto('/people');
    await personRow(page, DELETE_NAME).locator('[data-action="delete"]').click();

    // Deleting asks for confirmation first.
    const dialog = page.locator('#global-confirm-dialog');
    await expect(dialog).toHaveClass(/active/);
    await expect(dialog).toContainText(DELETE_NAME);
    await page.locator('#confirm-dialog-confirm').click();

    await expect(flashSuccess(page)).toContainText(DELETE_NAME);
    await expect(personRow(page, DELETE_NAME)).toHaveCount(0);

    // The person's face returns to the unassigned pool.
    await waitForFaceBackInPool(page, faceId);
  });

  test('people_can_view_and_remove_faces', async ({ page, request }) => {
    const personId = await createPersonViaApi(request, FACES_NAME);
    const faceId = await pickPoolFaceId(page);
    await assignFaceToPersonViaApi(request, faceId, personId);
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

    await request.post(`/people/${personId}/delete`);
  });
});
