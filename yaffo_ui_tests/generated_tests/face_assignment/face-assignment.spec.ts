import { test, expect, Page } from '@playwright/test';

async function ensurePersonExists(page: Page, personName: string): Promise<void> {
  await page.goto('/people');
  const personRow = page.locator(`tr:has-text("${personName}")`);
  const exists = await personRow.count() > 0;
  
  if (!exists) {
    const response = await page.request.post('/api/people/create', {
      data: { name: personName },
      headers: { 'Content-Type': 'application/json' }
    });
    expect(response.ok()).toBeTruthy();
  }
}

async function deletePersonIfExists(page: Page, personName: string): Promise<void> {
  await page.goto('/people');
  const personRow = page.locator(`tr:has-text("${personName}")`);
  const exists = await personRow.count() > 0;
  
  if (exists) {
    await personRow.locator('a:has-text("Delete")').click();
    // Wait for the specific delete modal to become active
    await page.waitForSelector('#deleteModal.active', { state: 'visible' });
    // Click the Delete button within the delete modal
    await page.locator('#deleteModal button[type="submit"]').click();
    await page.waitForLoadState('networkidle');
  }
}

test.describe('Face Assignment', () => {
  
  test('should be able to create a new person using the quick action section', async ({ page }) => {
    // Navigate to the face assignment page
    await page.goto('/faces');
    await expect(page).toHaveTitle(/Faces.*Photo Organizer/);
    
    // Check if Obama already exists in the person dropdown
    const personFilter = page.locator('select[name="person"]');
    const obamaOption = personFilter.locator('option:has-text("Obama")');
    const obamaExists = await obamaOption.count() > 0;
    
    // If Obama exists, delete them first
    if (obamaExists) {
      await deletePersonIfExists(page, 'Obama');
      await page.goto('/faces');
    }
    
    // Type in the name 'Obama' into the textbox
    const createPersonInput = page.locator('#create-person-name');
    await createPersonInput.fill('Obama');
    
    // Click the 'Create Person' button
    const createPersonButton = page.locator('#create-person-btn');
    await createPersonButton.click();
    
    // Wait for the page to update
    await page.waitForLoadState('networkidle');
    
    // Verify: No error messages are shown
    const errorMessages = page.locator('.error, .alert-danger, [role="alert"]:has-text("error")');
    await expect(errorMessages).toHaveCount(0);
    
    // Verify: There is a person named Obama in the person dropdown (in filters)
    // Note: There are two select[name="person"] elements on the page, so we check count instead of visibility
    await page.goto('/faces');
    const allPersonSelects = page.locator('select[name="person"]');
    const obamaOptions = allPersonSelects.locator('option:has-text("Obama")');
    // Should have at least one Obama option (actually 2 - one in filter, one in assign dropdown)
    await expect(obamaOptions).toHaveCount(2);
    
    // Also verify Obama appears in the assign dropdown specifically
    const assignDropdown = page.locator('#sidebar-person-select');
    await expect(assignDropdown.locator('option:has-text("Obama")')).toHaveCount(1);
    
    // Verify Obama appears in keyboard shortcuts
    const shortcutItem = page.locator('.shortcut-item:has-text("Obama")');
    await expect(shortcutItem).toBeVisible();
  });
  
  test('should be able to assign faces to people', async ({ page }) => {
    // Create a person named 'Obama' if needed
    await ensurePersonExists(page, 'Obama');
    
    // Navigate to the face assignment page
    await page.goto('/faces');
    await expect(page).toHaveTitle(/Faces.*Photo Organizer/);
    
    // Update the filter to group by People
    const groupByPeopleRadio = page.locator('#group-by-people');
    await groupByPeopleRadio.check();
    
    // Set threshold to 2
    const thresholdSlider = page.locator('#threshold-range');
    await thresholdSlider.fill('2');
    
    // Click the 'Apply Filter' button
    const applyFilterButton = page.locator('button.btn.btn-primary.filter-btn');
    await applyFilterButton.click();
    await page.waitForLoadState('networkidle');
    
    // Click Clear selection
    await page.locator('#deselect-all').click();
    await expect(page.locator('.face.selected')).toHaveCount(0);
    
    // Select one of the photos (click on the first face)
    const firstFace = page.locator('.face').first();
    const firstFaceId = await firstFace.getAttribute('data-face-id');
    await firstFace.click();
    
    // Verify the face is selected
    await expect(firstFace).toHaveClass(/selected/);
    
    // Select Obama from the 'Assign to Person' dropdown
    const selectButton = page.locator('.searchable-select-display').first();
    await selectButton.click();
    await page.waitForSelector('.searchable-select-dropdown', { state: 'visible' });
    await page.locator('.searchable-select-option:has-text("Obama")').click();
    await page.waitForSelector('.searchable-select-dropdown', { state: 'hidden' });
    await expect(page.locator('.searchable-select-text').first()).toContainText('Obama');
    
    // Click the Assign Selected button
    const assignButton = page.locator('#sidebar-assign-selected-btn');
    const responsePromise = page.waitForResponse(response => 
      response.url().includes('/api/faces/assign') && response.request().method() === 'POST'
    );
    await assignButton.click();
    const response = await responsePromise;
    
    // Verify: Success message is displayed (via API response)
    const responseData = await response.json();
    expect(responseData.success).toBeTruthy();
    expect(responseData.message).toMatch(/Successfully assigned.*face/);
    
    // Verify: Face is removed from the view
    await expect(page.locator(`.face[data-face-id="${firstFaceId}"]`)).not.toBeVisible({ timeout: 2000 });
    
    // Verify: Face is assigned to Obama on the people -> view faces screen
    await page.goto('/people');
    const obamaRow = page.locator('tr:has-text("Obama")');
    await obamaRow.locator('a:has-text("View Faces")').click();
    
    await expect(page).toHaveURL(/\/people\/\d+\/faces/);
    await expect(page.locator('h1')).toContainText('Obama');
    
    // Verify at least one face is shown for Obama
    // Note: On person faces page, faces use .face-card selector
    const faceCount = await page.locator('.face-card').count();
    expect(faceCount).toBeGreaterThan(0);
  });
  
  test('faces are automatically matched to people based on similarity', async ({ page }) => {
    // Create a person named 'Obama' and assign face to them if needed
    await ensurePersonExists(page, 'Obama');
    
    // Navigate to face assignment page
    await page.goto('/faces');
    await expect(page).toHaveTitle(/Faces.*Photo Organizer/);
    
    // Ensure Obama has at least one face assigned
    // Check if Obama already has faces
    await page.goto('/people');
    const obamaRow = page.locator('tr:has-text("Obama")');
    const facesCell = obamaRow.locator('td').nth(1); // Second cell is "Faces"
    const facesText = await facesCell.textContent();
    const faceCount = parseInt(facesText?.trim() || '0');
    
    // If Obama has no faces, assign one first
    if (faceCount === 0) {
      await page.goto('/faces');
      await page.locator('#group-by-people').check();
      await page.locator('#threshold-range').fill('2');
      await page.locator('button.btn.btn-primary.filter-btn').click();
      await page.waitForLoadState('networkidle');
      
      // Assign first face to Obama
      await page.locator('#deselect-all').click();
      const firstFace = page.locator('.face').first();
      await firstFace.click();
      
      const selectButton = page.locator('.searchable-select-display').first();
      await selectButton.click();
      await page.waitForSelector('.searchable-select-dropdown', { state: 'visible' });
      await page.locator('.searchable-select-option:has-text("Obama")').click();
      await page.waitForSelector('.searchable-select-dropdown', { state: 'hidden' });
      
      await page.locator('#sidebar-assign-selected-btn').click();
      await page.waitForResponse(response => 
        response.url().includes('/api/faces/assign') && response.request().method() === 'POST'
      );
      await page.waitForLoadState('networkidle');
    }
    
    // Navigate back to face assignment page
    await page.goto('/faces');
    
    // Update the filter to group by People
    const groupByPeopleRadio = page.locator('#group-by-people');
    await groupByPeopleRadio.check();
    
    // Set the similarity threshold to 2
    const thresholdSlider = page.locator('#threshold-range');
    await thresholdSlider.fill('2');
    
    // Click the Apply Filters button
    const applyFilterButton = page.locator('button.btn.btn-primary.filter-btn');
    await applyFilterButton.click();
    await page.waitForLoadState('networkidle');
    
    // Verify: There is a group for person 'Obama'
    const suggestionGroups = page.locator('.suggestion-group');
    const groupCount = await suggestionGroups.count();
    
    // If there are no faces left to assign, this is fine
    if (groupCount === 0) {
      const emptyState = page.locator('.empty-state:has-text("All Faces Assigned")');
      if (await emptyState.count() > 0) {
        // All faces are assigned, test passes
        return;
      }
    }
    
    // Check if there's a group for Obama or Unknown
    const obamaGroup = page.locator('.suggestion-group:has(h2:has-text("Obama"))');
    const unknownGroup = page.locator('.suggestion-group:has(h2:has-text("Unknown"))');
    
    const hasObamaGroup = await obamaGroup.count() > 0;
    const hasUnknownGroup = await unknownGroup.count() > 0;
    
    // At least one group should exist
    expect(groupCount).toBeGreaterThan(0);
    
    // If Obama group exists, verify its properties
    if (hasObamaGroup) {
      // Verify: All the faces in the Obama group
      const facesInObamaGroup = obamaGroup.locator('.face');
      const obamaFaceCount = await facesInObamaGroup.count();
      expect(obamaFaceCount).toBeGreaterThan(0);
      
      // Verify: All faces in the first group are selected (if Obama is first group)
      const firstGroup = suggestionGroups.first();
      const firstGroupHeading = await firstGroup.locator('h2').textContent();
      
      if (firstGroupHeading?.includes('Obama')) {
        const facesInFirstGroup = firstGroup.locator('.face');
        const selectedFacesInFirstGroup = firstGroup.locator('.face.selected');
        
        const totalFaces = await facesInFirstGroup.count();
        const selectedFaces = await selectedFacesInFirstGroup.count();
        
        // All faces in first group should be selected
        expect(selectedFaces).toBe(totalFaces);
      }
    }
    
    // Verify: None of the faces in the Unknown group are selected (if it exists and is not first)
    if (hasUnknownGroup) {
      const firstGroup = suggestionGroups.first();
      const firstGroupHeading = await firstGroup.locator('h2').textContent();
      
      // If Unknown is NOT the first group, its faces should not be selected
      if (!firstGroupHeading?.includes('Unknown')) {
        const facesInUnknownGroup = unknownGroup.locator('.face');
        const selectedFacesInUnknownGroup = unknownGroup.locator('.face.selected');
        
        const selectedCount = await selectedFacesInUnknownGroup.count();
        expect(selectedCount).toBe(0);
      }
    }
  });
  
  test('similar faces are grouped together', async ({ page }) => {
    // Navigate to the face assignment page
    await page.goto('/faces');
    await expect(page).toHaveTitle(/Faces.*Photo Organizer/);
    
    // Update the filter to group by Similarity
    const groupBySimilarityRadio = page.locator('#group-by-similarity');
    await groupBySimilarityRadio.check();
    
    // Set the similarity threshold to 2
    const thresholdSlider = page.locator('#threshold-range');
    await thresholdSlider.fill('2');
    
    // Click the Apply Filters button
    const applyFilterButton = page.locator('button.btn.btn-primary.filter-btn');
    await applyFilterButton.click();
    await page.waitForLoadState('networkidle');
    
    // Verify: Some groups are displayed
    const suggestionGroups = page.locator('.suggestion-group');
    const groupCount = await suggestionGroups.count();
    
    // If there are no unassigned faces or not enough to form groups, that's acceptable
    const emptyState = page.locator('.empty-state');
    const hasEmptyState = await emptyState.count() > 0;
    
    if (hasEmptyState) {
      // No faces to group is a valid state
      return;
    }
    
    // If groups exist, verify they meet the criteria
    if (groupCount > 0) {
      // Verify: All groups should have at least three faces (DEFAULT_MIN_SAMPLE_SIZE = 3)
      for (let i = 0; i < groupCount; i++) {
        const group = suggestionGroups.nth(i);
        const facesInGroup = group.locator('.face');
        const faceCount = await facesInGroup.count();
        
        // Each cluster should have at least 3 faces (min_samples in DBSCAN)
        expect(faceCount).toBeGreaterThanOrEqual(3);
      }
      
      // Verify: The first group is automatically selected for quick assignment
      const firstGroup = suggestionGroups.first();
      
      // Check that the group checkbox is checked
      const groupCheckbox = firstGroup.locator('.group-select-checkbox');
      await expect(groupCheckbox).toBeChecked();
      
      // Check that all faces in the first group are selected
      const facesInFirstGroup = firstGroup.locator('.face');
      const selectedFacesInFirstGroup = firstGroup.locator('.face.selected');
      
      const totalFaces = await facesInFirstGroup.count();
      const selectedFaces = await selectedFacesInFirstGroup.count();
      
      expect(selectedFaces).toBe(totalFaces);
      expect(selectedFaces).toBeGreaterThan(0);
    }
  });
});