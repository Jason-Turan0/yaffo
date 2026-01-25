import { test, expect, Page } from '@playwright/test';

// Test data: Faces belonging to Obama
const OBAMA_FACE_IDS = [1, 11, 13, 18, 26, 37, 41];

type PersonInfo = {
  id: number;
  name: string;
};

// Helper function to ensure person exists (create if needed)
async function ensurePersonExists(page: Page, personName: string): Promise<PersonInfo> {
  await page.goto('/faces');
  
  // Check if person exists in dropdown by getting all option text
  // Note: Options in select elements are hidden by default, cannot use waitForSelector with visible
  const personOptions = await page.locator('#sidebar-person-select option').allTextContents();
  const personExists = personOptions.some(text => text.trim() === personName);
  
  if (!personExists) {
    // Create the person via quick action
    await page.fill('#create-person-name', personName);
    await page.click('#create-person-btn');
    
    // Wait for page reload - app uses setTimeout(1500ms) before reload
    // Must wait for the delayed reload to complete
    await page.waitForTimeout(1600);
    await page.waitForLoadState('networkidle');
  }
  
  // Get person ID from dropdown
  const personOption = page.locator(`#sidebar-person-select option`).filter({ hasText: personName });
  const personId = await personOption.getAttribute('value');
  
  return {
    id: parseInt(personId || '0'),
    name: personName
  };
}

// Helper function to delete a person
async function deletePerson(page: Page, personName: string): Promise<void> {
  await page.goto('/people');
  
  // Find the person row and click delete
  const personRow = page.locator('tr').filter({ hasText: personName });
  
  if (await personRow.count() > 0) {
    const deleteLink = personRow.locator('a.delete');
    await deleteLink.click();
    
    // Confirm deletion in modal
    await page.click('button.btn-danger:has-text("Delete")');
    
    // Wait for redirect
    await page.waitForURL('/people');
  }
}

test.describe('Face Assignment', () => {
  test('should be able to create a new person using the quick action section', async ({ page }) => {
    await page.goto('/faces');
    
    // First, check if Obama exists and delete if so
    const personOptions = await page.locator('#sidebar-person-select option').allTextContents();
    const obamaExists = personOptions.some(text => text.trim() === 'Obama');
    
    if (obamaExists) {
      await deletePerson(page, 'Obama');
      await page.goto('/faces');
    }
    
    // Type in the name 'Obama' and click Create Person
    await page.fill('#create-person-name', 'Obama');
    await page.click('#create-person-btn');
    
    // Wait for page to reload - the app uses setTimeout(1500ms) before reloading
    // Must wait for the delayed reload to start and complete
    await page.waitForTimeout(1600);
    await page.waitForLoadState('networkidle');
    
    // Verify: No error messages are shown
    const errorMessages = page.locator('.error, .alert-danger');
    await expect(errorMessages).toHaveCount(0);
    
    // Verify: There is a person named Obama in the person dropdown
    // Note: Options are hidden in select elements, check via allTextContents
    const updatedOptions = await page.locator('#sidebar-person-select option').allTextContents();
    const obamaInDropdown = updatedOptions.some(text => text.trim() === 'Obama');
    expect(obamaInDropdown).toBe(true);
  });

  test('should be able to assign faces to people', async ({ page }) => {
    // Setup: Create person if needed
    const obama = await ensurePersonExists(page, 'Obama');
    
    await page.goto('/faces');
    
    // Update filter to group by People
    await page.check('#group-by-people');
    
    // Click Apply Filters
    await page.click('button.btn.btn-primary.filter-btn');
    await page.waitForLoadState('networkidle');
    
    // Clear selection
    await page.click('#deselect-all');
    
    // Select one of the photos that belong to Obama (face 1)
    const face1 = page.locator('.face[data-face-id="1"]');
    await face1.click();
    
    // Verify face is selected
    await expect(face1).toHaveClass(/selected/);
    
    // Select Obama from dropdown using searchable select display
    const selectDisplay = page.locator('.searchable-select-display');
    await selectDisplay.click();
    
    const obamaOption = page.locator('.searchable-select-option').filter({ hasText: 'Obama' });
    await obamaOption.click();
    
    // Click Assign Selected button
    await page.click('#sidebar-assign-selected-btn');
    
    // Wait for the assignment to complete
    await page.waitForResponse(response => 
      response.url().includes('/api/faces/assign') && response.status() === 200
    );
    
    // Wait for face to be removed from DOM
    await page.waitForTimeout(500);
    
    // Verify: Face 1 is removed from the view
    await expect(face1).toBeHidden();
    
    // Verify: Face 1 is assigned to Obama on the people -> view faces screen
    await page.goto(`/people/${obama.id}/faces`);
    const assignedFace = page.locator('.face-card[data-face-id="1"]');
    await expect(assignedFace).toBeVisible();
    
    // Cleanup: Delete person Obama
    await deletePerson(page, 'Obama');
  });

  test('faces are automatically matched to people based on similarity', async ({ page }) => {
    // Setup: Create person and assign face 1
    const obama = await ensurePersonExists(page, 'Obama');
    
    await page.goto('/faces');
    await page.check('#group-by-people');
    await page.click('button.btn.btn-primary.filter-btn');
    await page.waitForLoadState('networkidle');
    
    // Assign face 1 to Obama
    await page.click('#deselect-all');
    const face1 = page.locator('.face[data-face-id="1"]');
    await face1.click();
    
    // Select Obama using searchable select
    const selectDisplay = page.locator('.searchable-select-display');
    await selectDisplay.click();
    const obamaOption = page.locator('.searchable-select-option').filter({ hasText: 'Obama' });
    await obamaOption.click();
    
    await page.click('#sidebar-assign-selected-btn');
    
    await page.waitForResponse(response => 
      response.url().includes('/api/faces/assign') && response.status() === 200
    );
    
    // Wait for assignment to complete
    await page.waitForTimeout(500);
    
    // Now test the actual scenario
    await page.goto('/faces');
    
    // Update filter to group by People and set threshold to 2
    await page.check('#group-by-people');
    await page.fill('#threshold-range', '2');
    await page.click('button.btn.btn-primary.filter-btn');
    await page.waitForLoadState('networkidle');
    
    // Verify: There is a group for person 'Obama'
    const obamaGroup = page.locator('.suggestion-group').filter({ hasText: 'Obama' }).first();
    await expect(obamaGroup).toBeVisible();
    
    // Verify: All faces in the group are one of the expected face ids
    const facesInObamaGroup = await obamaGroup.locator('.face[data-face-id]').all();
    for (const face of facesInObamaGroup) {
      const faceId = await face.getAttribute('data-face-id');
      expect(OBAMA_FACE_IDS).toContain(parseInt(faceId || '0'));
    }
    
    // Verify: All faces in the first group are selected
    const firstGroup = page.locator('.suggestion-group').first();
    const facesInFirstGroup = await firstGroup.locator('.face').all();
    for (const face of facesInFirstGroup) {
      await expect(face).toHaveClass(/selected/);
    }
    
    // Verify: None of the faces in the Unknown group are selected
    const unknownGroup = page.locator('.suggestion-group').filter({ hasText: 'Unknown' });
    if (await unknownGroup.count() > 0) {
      const facesInUnknownGroup = await unknownGroup.locator('.face').all();
      for (const face of facesInUnknownGroup) {
        await expect(face).not.toHaveClass(/selected/);
      }
    }
    
    // Cleanup
    await deletePerson(page, 'Obama');
  });

  test('similar faces are grouped together', async ({ page }) => {
    await page.goto('/faces');
    
    // Update filter to group by Similarity
    await page.check('#group-by-similarity');
    
    // Set similarity threshold to 2
    await page.fill('#threshold-range', '2');
    
    // Click Apply Filters
    await page.click('button.btn.btn-primary.filter-btn');
    await page.waitForLoadState('networkidle');
    
    // Verify: Some groups are displayed
    const groups = page.locator('.suggestion-group');
    await expect(groups).not.toHaveCount(0);
    
    // Verify: All groups should have at least three faces
    const groupCount = await groups.count();
    for (let i = 0; i < groupCount; i++) {
      const group = groups.nth(i);
      const facesInGroup = group.locator('.face');
      const faceCount = await facesInGroup.count();
      expect(faceCount).toBeGreaterThanOrEqual(3);
    }
    
    // Verify: The first group is automatically selected
    const firstGroup = groups.first();
    const firstGroupCheckbox = firstGroup.locator('.group-select-checkbox');
    await expect(firstGroupCheckbox).toBeChecked();
    
    // Verify: All faces in the first group are selected
    const facesInFirstGroup = firstGroup.locator('.face');
    const firstGroupFaceCount = await facesInFirstGroup.count();
    for (let i = 0; i < firstGroupFaceCount; i++) {
      await expect(facesInFirstGroup.nth(i)).toHaveClass(/selected/);
    }
  });

  test('keyboard shortcuts enable quick face assignment', async ({ page }) => {
    // Navigate to people page and create person
    await page.goto('/people');
    
    // Delete TestKeyboardPerson if it exists
    const testPersonRow = page.locator('tr').filter({ hasText: 'TestKeyboardPerson' });
    if (await testPersonRow.count() > 0) {
      await testPersonRow.locator('a.delete').click();
      await page.click('button.btn-danger:has-text("Delete")');
      await page.waitForURL('/people');
    }
    
    // Create TestKeyboardPerson
    await page.click('button:has-text("Add Person")');
    await page.fill('#personName', 'TestKeyboardPerson');
    await page.click('button[type="submit"]');
    await page.waitForURL('/people');
    
    // Navigate to face assignment page
    await page.goto('/faces');
    
    // Update filter to group by People
    await page.check('#group-by-people');
    
    // Set similarity threshold to 2
    await page.fill('#threshold-range', '2');
    
    // Click Apply Filters
    await page.click('button.btn.btn-primary.filter-btn');
    await page.waitForLoadState('networkidle');
    
    // Note the keyboard shortcut for TestKeyboardPerson
    const shortcutItem = page.locator('.shortcut-item').filter({ hasText: 'TestKeyboardPerson' });
    await expect(shortcutItem).toBeVisible();
    const shortcutText = await shortcutItem.locator('kbd').textContent();
    const shortcutNumber = shortcutText?.trim() || '1';
    
    // Get the currently selected faces (first group should be auto-selected)
    const selectedFaces = page.locator('.face.selected');
    const selectedCount = await selectedFaces.count();
    expect(selectedCount).toBeGreaterThan(0);
    
    // Get the face IDs before assignment (as strings, since API returns strings)
    const selectedFaceIds: string[] = [];
    for (let i = 0; i < selectedCount; i++) {
      const faceId = await selectedFaces.nth(i).getAttribute('data-face-id');
      if (faceId) selectedFaceIds.push(faceId);
    }
    
    // Setup network listener to capture the response
    const responsePromise = page.waitForResponse(
      response => response.url().includes('/api/faces/assign') && response.status() === 200
    );
    
    // Press the keyboard shortcut
    await page.keyboard.press(shortcutNumber);
    
    // Wait for assignment to complete
    const response = await responsePromise;
    const responseData = await response.json();
    
    // Verify: Success message is displayed
    expect(responseData.success).toBe(true);
    expect(responseData.message).toContain('TestKeyboardPerson');
    
    // Verify: The correct faces were assigned
    // Note: API returns face_ids as strings, not numbers
    expect(responseData.face_ids).toEqual(expect.arrayContaining(selectedFaceIds));
    
    // Wait for DOM updates
    await page.waitForTimeout(500);
    
    // Verify: The faces are no longer visible
    for (const faceId of selectedFaceIds) {
      const face = page.locator(`.face[data-face-id="${faceId}"]`);
      await expect(face).toBeHidden();
    }
    
    // Verify: The next group is automatically selected (if there are more groups)
    const remainingGroups = page.locator('.suggestion-group');
    const remainingGroupCount = await remainingGroups.count();
    
    if (remainingGroupCount > 0) {
      const firstVisibleGroup = remainingGroups.first();
      const firstVisibleGroupFaces = firstVisibleGroup.locator('.face.selected');
      const newSelectedCount = await firstVisibleGroupFaces.count();
      expect(newSelectedCount).toBeGreaterThan(0);
    }
    
    // Cleanup: Delete TestKeyboardPerson
    await page.goto('/people');
    const cleanupPersonRow = page.locator('tr').filter({ hasText: 'TestKeyboardPerson' });
    await cleanupPersonRow.locator('a.delete').click();
    await page.click('button.btn-danger:has-text("Delete")');
    await page.waitForURL('/people');
  });
});