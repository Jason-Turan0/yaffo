import { test, expect } from '@playwright/test';

test.describe('Photo Details - File Information', () => {
  const PHOTO_ID = 14;

  test('photo_details_displays_file_information', async ({ page, baseURL }) => {
    // Navigate to the photo details page
    await page.goto(`/media/view/${PHOTO_ID}`);

    // Verify page loads successfully
    await expect(page.locator('h2').filter({ hasText: 'Photo Details' })).toBeVisible();

    // Verify file name is displayed in the sidebar (scope to the File
    // Information section — the Location section can also have a "Name:" item)
    const fileInfoSection = page.locator('.detail-section').filter({ hasText: 'File Information' });
    await expect(fileInfoSection).toBeVisible();
    const fileNameElement = fileInfoSection.locator('.detail-item:has-text("Name:") .detail-value');
    await expect(fileNameElement).toContainText('DSCN0010.jpg');

    // Verify folder path is displayed
    const folderPathElement = fileInfoSection.locator('.detail-item:has-text("Folder:") .detail-value');
    await expect(folderPathElement).toBeVisible();

    // Verify the main photo image loads correctly
    const mainPhoto = page.locator('#mainPhoto');
    await expect(mainPhoto).toBeVisible();

    // Check that the image source is correct and returns 200
    const imageResponse = await page.request.get(`/media/${PHOTO_ID}`);
    expect(imageResponse.status()).toBe(200);

    // Verify Open File and Open Folder buttons are visible
    const openFileButton = page.locator('button.action-button').filter({ hasText: 'Open File' });
    const openFolderButton = page.locator('button.action-button').filter({ hasText: 'Open Folder' });
    await expect(openFileButton).toBeVisible();
    await expect(openFolderButton).toBeVisible();
  });
});