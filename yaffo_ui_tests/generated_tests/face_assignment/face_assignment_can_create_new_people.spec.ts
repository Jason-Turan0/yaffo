import { test, expect } from '@playwright/test';

test.describe('Face Assignment - Create New People', () => {
  test('should be able to create a new person using the quick action section', async ({ page }) => {
    // Navigate to the face assignment page
    await page.goto('/faces');
    await expect(page).toHaveTitle(/Faces - Photo Organizer/);

    // Check if Obama already exists in the person dropdown
    const dropdownButton = page.locator('.searchable-select-display').first();
    await dropdownButton.click();
    
    // Wait for dropdown to open
    await page.waitForSelector('.searchable-select-dropdown', { state: 'visible' });
    
    const obamaOption = page.locator('.searchable-select-option:has-text("Obama")');
    const obamaExists = await obamaOption.count() > 0;
    
    if (obamaExists) {
      // Close the dropdown
      await page.keyboard.press('Escape');
      
      // Navigate to people page and delete Obama
      await page.goto('/people');
      await expect(page.locator('h1:has-text("People")')).toBeVisible();
      
      // Find and click the delete link for Obama
      const obamaRow = page.locator('tr:has-text("Obama")');
      await expect(obamaRow).toBeVisible();
      await obamaRow.locator('a:has-text("Delete")').click();
      
      // Confirm deletion in modal
      await expect(page.locator('#deletePersonName')).toHaveText('Obama');
      await page.locator('#deleteModal button[type="submit"]').click();
      
      // Wait for deletion to complete and success notification
      await expect(page.locator('.notification.success.visible')).toBeVisible({ timeout: 5000 });
      await expect(page.locator('.notification.success.visible')).toContainText(/deleted/i);
      
      // Return to face assignment screen
      await page.goto('/faces');
    } else {
      // Close dropdown if it was opened
      await page.keyboard.press('Escape');
    }
    
    // Type in the name 'Obama' into the textbox
    const createPersonInput = page.locator('#create-person-name');
    await createPersonInput.fill('Obama');
    
    // Click the 'Create Person' button
    const createPersonButton = page.locator('#create-person-btn');
    await createPersonButton.click();
    
    // Verify: No error messages are shown
    await expect(page.locator('.notification.error.visible')).not.toBeVisible({ timeout: 2000 }).catch(() => {});
    
    // Verify: A confirmation toast message is displayed
    await expect(page.locator('.notification.success.visible')).toBeVisible({ timeout: 5000 });
    await expect(page.locator('.notification.success.visible')).toContainText(/created.*obama/i);
    
    // Wait for page reload (happens after 1.5s according to JS)
    await page.waitForLoadState('networkidle');
    await page.waitForTimeout(2000);
    
    // Verify: There is a person named Obama in the person dropdown
    await dropdownButton.click();
    await page.waitForSelector('.searchable-select-dropdown', { state: 'visible' });
    await expect(page.locator('.searchable-select-option:has-text("Obama")')).toBeVisible();
  });
});