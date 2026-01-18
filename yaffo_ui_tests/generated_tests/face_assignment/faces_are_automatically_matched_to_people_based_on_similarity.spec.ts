import { test, expect } from '@playwright/test';

test.describe('Face Assignment - Automatic Face Matching', () => {
  test('should automatically match similar faces to a person after initial assignment', async ({ page }) => {
    // Expected face IDs for Obama based on spec
    const obamaFaceIds = [1, 11, 13, 18, 26, 37, 41];
    
    // Helper function to create person and assign face if needed
    const setupObamaWithFace = async () => {
      // Check if Obama exists
      await page.goto('/people');
      const obamaExists = await page.locator('tr:has-text("Obama")').count() > 0;
      
      if (!obamaExists) {
        // Create Obama
        await page.locator('button:has-text("+ Add Person")').first().click();
        await expect(page.locator('#addModal')).toBeVisible();
        await page.locator('#addModal [name="name"]').fill('Obama');
        await page.locator('#addModal button[type="submit"]').click();
        await expect(page.locator('.notification.success.visible')).toBeVisible({ timeout: 5000 });
      }
      
      // Check if Obama has any faces assigned
      await page.goto('/people');
      const obamaRow = page.locator('tr:has-text("Obama")');
      const facesCountText = await obamaRow.locator('td:nth-child(2)').textContent();
      
      if (facesCountText && parseInt(facesCountText.trim()) === 0) {
        // Assign face 1 to Obama
        await page.goto('/faces?group_by=people&threshold=2');
        await page.waitForLoadState('networkidle');
        
        // Clear any existing selections
        await page.locator('#deselect-all').click();
        await page.waitForTimeout(500);
        
        const face1 = page.locator('.face[data-face-id="1"]');
        await expect(face1).toBeVisible();
        await face1.click();
        
        const dropdownButton = page.locator('.searchable-select-display').first();
        await dropdownButton.click();
        await page.waitForSelector('.searchable-select-dropdown', { state: 'visible' });
        await page.locator('.searchable-select-option:has-text("Obama")').click();
        
        const assignButton = page.locator('#sidebar-assign-selected-btn');
        await assignButton.click();
        await expect(page.locator('.notification.success.visible')).toBeVisible({ timeout: 5000 });
        await page.waitForTimeout(1000);
      }
    };
    
    // Ensure Obama exists with at least one face assigned
    await setupObamaWithFace();
    
    // Navigate to face assignment page
    await page.goto('/faces');
    await expect(page).toHaveTitle(/Faces - Photo Organizer/);
    
    // Update the filter to group by People
    await page.locator('#group-by-people').check();
    
    // Set the similarity threshold to 2
    const thresholdSlider = page.locator('#threshold-range');
    await thresholdSlider.fill('2');
    
    // Verify threshold display shows 2
    await expect(page.locator('#threshold-value')).toHaveText('2');
    
    // Click the Apply Filters button
    await page.locator('button:has-text("Apply Filters")').click();
    await page.waitForLoadState('networkidle');
    await page.waitForTimeout(1000);
    
    // Verify: There is a group for person 'Obama'
    const suggestionGroups = page.locator('.suggestion-group');
    const groupCount = await suggestionGroups.count();
    
    let obamaGroupFound = false;
    let obamaGroupIndex = -1;
    
    for (let i = 0; i < groupCount; i++) {
      const group = suggestionGroups.nth(i);
      const heading = group.locator('h2');
      const headingText = await heading.textContent();
      
      if (headingText && headingText.includes('Obama')) {
        obamaGroupFound = true;
        obamaGroupIndex = i;
        break;
      }
    }
    
    expect(obamaGroupFound).toBeTruthy();
    
    if (obamaGroupFound) {
      const obamaGroup = suggestionGroups.nth(obamaGroupIndex);
      
      // Get all faces in Obama's group
      const facesInGroup = obamaGroup.locator('.face');
      const faceCount = await facesInGroup.count();
      
      // Verify: All the faces in the group are one of the expected face ids for Obama
      for (let i = 0; i < faceCount; i++) {
        const face = facesInGroup.nth(i);
        const faceId = await face.getAttribute('data-face-id');
        const faceIdNum = parseInt(faceId || '0');
        
        // Check if this face ID is in the expected Obama face IDs
        expect(obamaFaceIds).toContain(faceIdNum);
      }
      
      // Verify: All the faces in the first group are selected
      // (First group should be Obama's group if it exists)
      const firstGroup = suggestionGroups.first();
      const firstGroupHeading = await firstGroup.locator('h2').textContent();
      
      if (firstGroupHeading && firstGroupHeading.includes('Obama')) {
        const firstGroupFaces = firstGroup.locator('.face');
        const firstGroupFaceCount = await firstGroupFaces.count();
        
        for (let i = 0; i < firstGroupFaceCount; i++) {
          const face = firstGroupFaces.nth(i);
          await expect(face).toHaveClass(/selected/);
        }
      }
    }
    
    // Verify: None of the faces in the Unknown group are selected
    const unknownGroup = page.locator('.suggestion-group:has(h2:has-text("Unknown"))');
    const unknownGroupExists = await unknownGroup.count() > 0;
    
    if (unknownGroupExists) {
      const unknownFaces = unknownGroup.locator('.face');
      const unknownFaceCount = await unknownFaces.count();
      
      for (let i = 0; i < unknownFaceCount; i++) {
        const face = unknownFaces.nth(i);
        await expect(face).not.toHaveClass(/selected/);
      }
    }
  });
});