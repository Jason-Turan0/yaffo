import { test, expect } from '@playwright/test';

test.describe('Face Assignment - Assign Faces to People', () => {
  test('should be able to assign faces to people', async ({ page }) => {
    // Helper function to create person if needed
    const ensurePersonExists = async (personName: string) => {
      await page.goto('/people');
      
      const personExists = await page.locator(`tr:has-text("${personName}")`).count() > 0;
      
      if (!personExists) {
        // Create the person
        await page.locator('button:has-text("+ Add Person")').first().click();
        await expect(page.locator('#addModal')).toBeVisible();
        await page.locator('#addModal [name="name"]').fill(personName);
        await page.locator('#addModal button[type="submit"]').click();
        await expect(page.locator('.notification.success.visible')).toBeVisible({ timeout: 5000 });
      }
    };
    
    // Create a person named 'Obama' if needed
    await ensurePersonExists('Obama');
    
    // Navigate to face assignment page
    await page.goto('/faces');
    await expect(page).toHaveTitle(/Faces - Photo Organizer/);
    
    // Update the filter to group by People
    await page.locator('#group-by-people').check();
    
    // Click the 'Apply Filter' button
    await page.locator('button:has-text("Apply Filters")').click();
    await page.waitForLoadState('networkidle');
    
    // Verify we're in group by people mode
    await expect(page.locator('#group-by-people')).toBeChecked();
    
    // Click Clear selection
    await page.locator('#deselect-all').click();
    await page.waitForTimeout(500);
    
    // Verify faces are deselected
    const selectedFaces = await page.locator('.face.selected').count();
    expect(selectedFaces).toBe(0);
    
    // Select face 1 by clicking on it
    const face1 = page.locator('.face[data-face-id="1"]');
    await expect(face1).toBeVisible();
    await face1.click();
    
    // Verify face 1 is selected
    await expect(face1).toHaveClass(/selected/);
    
    // Select Obama from the 'Assign to Person' dropdown
    const dropdownButton = page.locator('.searchable-select-display').first();
    await dropdownButton.click();
    await page.waitForSelector('.searchable-select-dropdown', { state: 'visible' });
    await page.locator('.searchable-select-option:has-text("Obama")').click();
    
    // Verify Obama is selected in dropdown (button text should update)
    await expect(dropdownButton).toContainText('Obama');
    
    // Click the Assign Selected button
    const assignButton = page.locator('#sidebar-assign-selected-btn');
    await assignButton.click();
    
    // Verify: Success message is displayed
    await expect(page.locator('.notification.success.visible')).toBeVisible({ timeout: 5000 });
    await expect(page.locator('.notification.success.visible')).toContainText(/assigned.*face.*obama/i);
    
    // Verify: Face 1 is removed from the view (should fade out and be removed from DOM)
    await expect(face1).not.toBeAttached({ timeout: 3000 });
    
    // Verify: Face 1 is assigned to Obama on the people -> view faces screen
    await page.goto('/people');
    const obamaRow = page.locator('tr:has-text("Obama")');
    await expect(obamaRow).toBeVisible();
    await obamaRow.locator('a:has-text("View Faces")').click();
    
    // Wait for person faces page to load
    await expect(page.locator('h1:has-text("Obama\'s Faces")')).toBeVisible();
    
    // Verify face 1 is present on Obama's faces page
    const obamaFace1 = page.locator('.face-card[data-face-id="1"]');
    await expect(obamaFace1).toBeVisible();
  });
});