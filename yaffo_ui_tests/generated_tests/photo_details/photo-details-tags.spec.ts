import { test, expect } from '@playwright/test';

test.describe('Photo Details - Edit Tags', () => {
  const PHOTO_ID = 14;
  // Use timestamp to make tag name unique across test runs/retries
  const TEST_TAG_NAME = `TestTag_${Date.now()}`;
  const TEST_TAG_VALUE = 'TestValue';
  let createdTagId: number | null = null;

  // Cleanup after test using API
  test.afterEach(async ({ request }) => {
    if (createdTagId) {
      try {
        await request.delete(`/api/photo/tags/${createdTagId}`);
      } catch (error) {
        console.log('Cleanup failed:', error);
      }
    }
  });

  test('photo_details_can_edit_tags', async ({ page, request }) => {
    // Navigate to the photo details page
    await page.goto(`/photo/view/${PHOTO_ID}`);

    // Wait for page to load
    await expect(page.locator('h2').filter({ hasText: 'Photo Details' })).toBeVisible();

    // Find and click the Edit Tags button
    const editTagsButton = page.locator('button.action-button').filter({ hasText: 'Edit Tags' });
    await expect(editTagsButton).toBeVisible();
    await editTagsButton.click();

    // Verify Edit Tags modal opens
    const modal = page.locator('#tagsModal');
    await expect(modal).toBeVisible();
    await expect(modal.locator('#modalTitle')).toHaveText('Edit Tags');

    // Verify new tag input fields are visible
    const tagNameInput = page.locator('#modal-new-tag-name');
    const tagValueInput = page.locator('#modal-new-tag-value');
    await expect(tagNameInput).toBeVisible();
    await expect(tagValueInput).toBeVisible();

    // Count existing tags before adding
    const tagsEditorList = page.locator('#tags-editor-list');
    const existingTagsCount = await tagsEditorList.locator('.tag-editor-item').count();

    // Enter tag name and value
    await tagNameInput.fill(TEST_TAG_NAME);
    await tagValueInput.fill(TEST_TAG_VALUE);

    // Click the Add Tag button
    const addTagButton = page.locator('button.btn.btn-secondary').filter({ hasText: '+ Add Tag' });
    await expect(addTagButton).toBeVisible();
    await addTagButton.click();

    // Verify the tag appears in the editor list - check that count increased
    await expect(tagsEditorList.locator('.tag-editor-item')).toHaveCount(existingTagsCount + 1);
    
    // Verify our specific tag is visible in the editor list
    // The tag name is in an input field, so we need to check by input value
    const newTagItem = tagsEditorList.locator('.tag-editor-item').last();
    await expect(newTagItem).toBeVisible();
    
    // Verify the inputs have the correct values
    const nameInputInEditor = newTagItem.locator('input.tag-input').first();
    const valueInputInEditor = newTagItem.locator('input.tag-input').last();
    await expect(nameInputInEditor).toHaveValue(TEST_TAG_NAME);
    await expect(valueInputInEditor).toHaveValue(TEST_TAG_VALUE);

    // Click Save Changes
    const saveButton = page.locator('#tagsModal button[type="submit"]').filter({ hasText: 'Save Changes' });
    await expect(saveButton).toBeVisible();
    await saveButton.click();

    // Wait for page reload after save
    await page.waitForLoadState('networkidle');

    // Verify the new tag appears in the Tags section on the page
    const tagsSection = page.locator('.detail-section').filter({ hasText: 'Tags' });
    await expect(tagsSection).toBeVisible();
    
    const tagsList = tagsSection.locator('.tags-list');
    const newTag = tagsList.locator('.tag-item').filter({ hasText: TEST_TAG_NAME });
    // Use first() in case there are duplicates from previous failed test runs
    await expect(newTag.first()).toBeVisible();
    
    // Verify tag displays both name and value
    await expect(newTag.first().locator('.tag-name')).toHaveText(TEST_TAG_NAME);
    await expect(newTag.first().locator('.tag-value')).toHaveText(TEST_TAG_VALUE);

    // Get the tag ID from the page for cleanup via API
    // Extract tag ID from the page content (it's in the JavaScript initialization)
    const response = await request.get(`/photo/view/${PHOTO_ID}`);
    const htmlContent = await response.text();
    const tagDataMatch = htmlContent.match(/tags_data[\s]*=[\s]*\[([^\]]+)\]/);
    if (tagDataMatch) {
      const tagsData = JSON.parse(`[${tagDataMatch[1]}]`);
      const createdTag = tagsData.find((t: any) => t.tag_name === TEST_TAG_NAME);
      if (createdTag) {
        createdTagId = createdTag.id;
      }
    }

    // Note: Cleanup of the created tag is handled by test.afterEach using the API
    // This ensures test data doesn't persist even if the test fails
  });
});