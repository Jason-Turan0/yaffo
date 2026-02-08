import { test, expect, Page, APIRequestContext } from '@playwright/test';

// Test data: Faces known to belong to Obama
const OBAMA_FACE_IDS = new Set([11, 13, 18, 26, 37, 41]);

// Type definition for person data
type PersonInfo = {
  id: number;
  name: string;
};

/**
 * Deletes a person through the UI.
 * This is preferred for cleanup to ensure the application state is fully reset.
 * @param page The Playwright Page object.
 * @param personName The name of the person to delete.
 */
async function deletePersonByName(page: Page, personName: string): Promise<void> {
  await page.goto('/people');
  const personRow = page.locator('tr').filter({ hasText: personName });
  if (await personRow.count() > 0) {
    await personRow.locator('a.delete').click();
    // Confirm deletion in the modal
    await page.locator('#deleteModal button.btn-danger').click();
    // Wait for page to reload and person to be removed from the list
    await page.waitForLoadState('domcontentloaded');
    // Verify person was actually deleted by checking they're not in the list
    await expect(personRow).toHaveCount(0, { timeout: 5000 });
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
        const viewFacesLink = personRow.locator('a:has-text("View Faces")');
        const href = await viewFacesLink.getAttribute('href');
        const personId = parseInt(href!.match(/\d+/)![0], 10);
        return { id: personId, name: personName };
    }

    expect(response.ok()).toBeTruthy();
    const data = await response.json();
    return { id: data.person_id, name: data.name };
}

/**
 * Assigns a face to a person using the API to speed up test setup.
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

test.describe('Face Assignment', () => {
  // Set a longer timeout for these tests since they involve UI interactions and cleanups
  test.setTimeout(30000);

  // Cleanup after each test to ensure isolation
  test.afterEach(async ({ page }) => {
    await deletePersonByName(page, 'Obama');
    await deletePersonByName(page, 'TestKeyboardPerson');
  });

  test('should be able to create a new person using the quick action section', async ({ page }) => {
    await deletePersonByName(page, 'Obama'); // Ensure clean state before test
    await page.goto('/faces');

    // Type in the name 'Obama' and click Create Person
    await page.locator('#create-person-name').fill('Obama');
    await page.locator('#create-person-btn').click();

    // Wait for the API call to succeed and the subsequent page reload to complete
    await page.waitForResponse(resp => resp.url().includes('/api/people/create') && resp.status() === 201);
    await page.waitForLoadState('domcontentloaded');

    // A toast isn't shown because the page reloads. The verification is that the person now exists.
    const personOption = page.locator('#sidebar-person-select option').filter({ hasText: 'Obama' });
    await expect(personOption).toHaveCount(1, { timeout: 5000 }); // Wait for dropdown to be populated
  });

  test('should be able to assign faces to people', async ({ page, request }) => {
    const obama = await createPersonViaApi(request, page, 'Obama');
    // Navigate to faces page with filter that shows all faces
    await page.goto('/faces?group_by=people');
    await page.waitForLoadState('networkidle');

    // Actions: Clear selection, select face 1, select person using the custom dropdown, and assign
    await page.locator('#deselect-all').click();
    const face1 = page.locator('.face[data-face-id="1"]');
    await expect(face1).toBeVisible({ timeout: 10000 });
    await face1.click();
    
    // Use the custom searchable dropdown instead of native selectOption
    await page.locator('.searchable-select-display').click();
    await page.locator('.searchable-select-option').filter({ hasText: 'Obama' }).click();
    
    await page.locator('#sidebar-assign-selected-btn').click();

    // Wait for API call and verify success notification
    await page.waitForResponse(resp => resp.url().includes('/api/faces/assign') && resp.ok());
    await expect(page.locator('.notification.visible')).toBeVisible();

    // Verify face 1 is removed from the view
    await expect(face1).not.toBeVisible();

    // Verify face is correctly assigned on the person's page
    await page.goto(`/people/${obama.id}/faces`);
    await expect(page.locator(`[data-face-id="1"]`)).toBeVisible();
  });

  test('faces are automatically matched to people based on similarity', async ({ page, request }) => {
    // Setup: Create 'Obama' and assign a known face to establish a baseline
    const obama = await createPersonViaApi(request, page, 'Obama');
    await assignFaceToPersonViaApi(request, 1, obama.id);

    await page.goto('/faces');

    // Filter by people with a low similarity threshold to create groups
    await page.locator('#group-by-people').check();
    await page.locator('#threshold-range').fill('2');
    await page.locator('button:has-text("Apply Filters")').click();
    await page.waitForLoadState('networkidle');

    // Verify a suggestion group for 'Obama' is now visible
    const obamaGroup = page.locator('.suggestion-group').filter({ hasText: 'Obama' });
    await expect(obamaGroup).toBeVisible();

    // Verify all faces in the group belong to Obama
    const facesInGroup = await obamaGroup.locator('.face').all();
    for (const face of facesInGroup) {
      const faceId = await face.getAttribute('data-face-id');
      expect(OBAMA_FACE_IDS.has(parseInt(faceId!, 10))).toBe(true);
    }
    
    // Verify faces in the first group are selected by default
    const firstGroupFaces = page.locator('.suggestion-group').first().locator('.face');
    await expect(firstGroupFaces.first()).toHaveClass(/selected/);

    // Verify faces in 'Unknown' group are not selected
    const unknownGroupFaces = page.locator('.suggestion-group:has-text("Unknown") .face');
    if (await unknownGroupFaces.count() > 0) {
        await expect(unknownGroupFaces.first()).not.toHaveClass(/selected/);
    }
  });

  test('similar faces are grouped together', async ({ page }) => {
    await page.goto('/faces');

    // Filter by similarity to cluster unknown faces
    await page.locator('#group-by-similarity').check();
    await page.locator('#threshold-range').fill('2');
    await page.locator('button:has-text("Apply Filters")').click();
    await page.waitForLoadState('networkidle');

    const groups = page.locator('.suggestion-group');
    await expect(groups.first()).toBeVisible();

    // Verify all groups meet the minimum size requirement
    for (const group of await groups.all()) {
      const faceCount = await group.locator('.face').count();
      expect(faceCount).toBeGreaterThanOrEqual(3);
    }

    // Verify the first group is selected by default for quick assignment
    const firstGroupCheckbox = groups.first().locator('.group-select-checkbox');
    await expect(firstGroupCheckbox).toBeChecked();
  });

  test('keyboard shortcuts enable quick face assignment', async ({ page, request }) => {
    await createPersonViaApi(request, page, 'TestKeyboardPerson');
    await page.goto('/faces');

    // Apply filters first to group faces and auto-select the first group
    await page.locator('#group-by-similarity').check();
    await page.locator('#threshold-range').fill('2');
    await page.locator('button:has-text("Apply Filters")').click();
    await page.waitForLoadState('networkidle');

    // Note the keyboard shortcut number displayed for the person
    const shortcutItem = page.locator('.shortcut-item').filter({ hasText: 'TestKeyboardPerson' });
    const shortcutKey = await shortcutItem.locator('kbd').textContent();
    expect(shortcutKey).toBeTruthy();

    // Get the IDs of the faces that are selected by default (in the first group)
    const selectedFaceLocators = page.locator('.suggestion-group').first().locator('.face.selected');
    const faceIds = await selectedFaceLocators.evaluateAll(elements => 
        elements.map(el => el.getAttribute('data-face-id'))
    );
    expect(faceIds.length).toBeGreaterThan(0);

    // Press the shortcut key to assign the faces
    await page.keyboard.press(shortcutKey!); 

    // Wait for API call and verify success notification
    await page.waitForResponse(resp => resp.url().includes('/api/faces/assign') && resp.ok());
    await expect(page.locator('.notification.visible')).toBeVisible();

    // Verify the assigned faces are no longer visible
    for (const faceId of faceIds) {
      await expect(page.locator(`.face[data-face-id="${faceId}"]`)).not.toBeVisible();
    }
  });

  test('face page navigation works', async ({ page }) => {
    await page.goto('/faces');

    // Set page size to 25 to enable pagination
    await page.locator('select[name="page-size"]').selectOption('25');
    await page.waitForURL(/page-size=25/);
    await page.waitForLoadState('networkidle');

    const subtitle = page.locator('.subtitle');
    const initialText = await subtitle.textContent();
    await expect(subtitle).toContainText(/Showing \d+ of \d+ unassigned/);

    // Extract total number of faces
    const totalFaces = parseInt(initialText!.match(/of (\d+)/)![1], 10);
    const facesOnPage1 = await page.locator('.face').count();
    expect(facesOnPage1).toBeLessThanOrEqual(25);

    if (totalFaces > 25) {
      // Test 'Next' button
      await page.getByRole('link', { name: 'Next' }).click();
      await expect(page).toHaveURL(/page=2/);
      await expect(subtitle).not.toHaveText(initialText!);

      // Test 'Last' button
      const lastPage = Math.ceil(totalFaces / 25);
      await page.getByRole('link', { name: 'Last' }).click();
      await expect(page).toHaveURL(new RegExp(`page=${lastPage}`));
      const facesOnLastPage = await page.locator('.face').count();
      expect(facesOnLastPage).toBeLessThanOrEqual(25);

      // Test 'First' button
      await page.getByRole('link', { name: 'First' }).click();
      await expect(page).toHaveURL(/page=1/);
      await expect(subtitle).toHaveText(initialText!);
    }
  });
});
