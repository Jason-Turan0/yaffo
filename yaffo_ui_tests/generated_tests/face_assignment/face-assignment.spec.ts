import { test, expect, Page, APIRequestContext } from '@playwright/test';

// Type definition for person data
type PersonInfo = {
  id: number;
  name: string;
};

// These tests share one server-side pool of unassigned faces and create/delete
// people, so they must not interleave.
test.describe.configure({ mode: 'serial', timeout: 30_000 });

/**
 * Deletes a person through the UI (people list -> Delete -> global confirm dialog).
 * Deleting a person also unassigns their faces, restoring the shared pool.
 * @param page The Playwright Page object.
 * @param personName The name of the person to delete.
 */
async function deletePersonByName(page: Page, personName: string): Promise<void> {
  await page.goto('/people');
  const personRow = page.locator('tr').filter({ hasText: personName });
  if (await personRow.count() > 0) {
    await personRow.locator('a.action-link.delete').click();
    // Confirm deletion in the global confirm dialog
    await page.locator('#confirm-dialog-confirm').click();
    // The page reloads after the POST; the person should be gone from the list
    await expect(personRow).toHaveCount(0);
  }
}

/**
 * Creates a person using the API to speed up test setup.
 * @param request The Playwright APIRequestContext object.
 * @param page The Playwright Page object.
 * @param personName The name of the person to create.
 * @returns The created person's information (id and name).
 */
async function createPersonViaApi(request: APIRequestContext, page: Page, personName: string): Promise<PersonInfo> {
    const response = await request.post('/api/people/create', {
        data: { name: personName },
        failOnStatusCode: false, // Don't fail if the person already exists (400)
    });

    if (response.status() === 400) {
        // If person exists, find them on the people page to get their ID
        await page.goto('/people');
        const personRow = page.locator('tr').filter({ hasText: personName });
        const personLink = personRow.locator('a.person-name.row-link');
        const href = await personLink.getAttribute('href');
        const personId = parseInt(href!.match(/\/people\/(\d+)\/faces/)![1], 10);
        return { id: personId, name: personName };
    }

    expect(response.ok()).toBeTruthy();
    const data = await response.json();
    return { id: data.person_id, name: data.name };
}

/**
 * Assigns a face to a person using the API to speed up test setup.
 * The server enqueues a background task, so completion is asynchronous —
 * use waitForFaceAssigned() before depending on the result.
 * @param request The Playwright APIRequestContext object.
 * @param faceId The ID of the face to assign.
 * @param personId The ID of the person to assign the face to.
 */
async function assignFaceToPersonViaApi(request: APIRequestContext, faceId: number, personId: number): Promise<void> {
    const response = await request.post('/api/faces/assign', {
        data: {
            faces: [faceId],
            person: personId,
            faceStatus: 'ASSIGNED'
        }
    });
    expect(response.ok()).toBeTruthy();
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
  // Cleanup after each test to ensure isolation (deleting a person also
  // returns their faces to the unassigned pool)
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

  test('should be able to assign faces to people', async ({ page, request }) => {
    const obama = await createPersonViaApi(request, page, 'Obama');

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

  test('faces are automatically matched to people based on similarity', async ({ page, request }) => {
    // Setup: Create 'Obama' and assign a known face to establish a baseline embedding
    const obama = await createPersonViaApi(request, page, 'Obama');
    await assignFaceToPersonViaApi(request, 1, obama.id);
    await waitForFaceAssigned(page, obama.id, 1);

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

  test('keyboard shortcuts enable quick face assignment', async ({ page, request }) => {
    const person = await createPersonViaApi(request, page, 'TestKeyboardPerson');
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
