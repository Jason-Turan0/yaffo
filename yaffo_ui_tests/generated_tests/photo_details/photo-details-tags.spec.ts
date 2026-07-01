import { test, expect } from '@playwright/test';

test.describe('Photo Details - Edit Tags', () => {
  const PHOTO_ID = 14;
  // Use timestamp to make tag name unique across test runs/retries
  const TEST_TAG_NAME = `TestTag_${Date.now()}`;
  const TEST_TAG_VALUE = 'TestValue';
  // Snapshot of the photo's tags before the test, used to restore state.
  // Tags are saved wholesale via PUT /api/media/<id>/tags (no per-tag delete).
  let originalTags: { tag_name: string; tag_value: string }[] | null = null;

  test.afterEach(async ({ request }) => {
    if (originalTags) {
      try {
        await request.put(`/api/media/${PHOTO_ID}/tags`, {
          data: { tags: originalTags },
        });
      } catch (error) {
        console.log('Cleanup failed:', error);
      }
    }
  });

  test('photo_details_can_edit_tags', async ({ page }) => {
    // Navigate to the photo details page
    await page.goto(`/media/view/${PHOTO_ID}`);

    // Wait for page to load
    await expect(page.locator('h2').filter({ hasText: 'Photo Details' })).toBeVisible();

    // Snapshot the current tags so afterEach can restore them
    const tagsSection = page.locator('.detail-section').filter({ hasText: 'Tag' });
    originalTags = await tagsSection.locator('.tag-item').evaluateAll(items =>
      items.map(item => ({
        tag_name: item.querySelector('.tag-name')?.textContent?.trim() ?? '',
        tag_value: item.querySelector('.tag-value')?.textContent?.trim() ?? '',
      }))
    );

    // Find and click the Edit Tags button
    const editTagsButton = page.locator('button.action-button').filter({ hasText: 'Edit Tags' });
    await expect(editTagsButton).toBeVisible();
    await editTagsButton.click();

    // Verify Edit Tags modal opens
    const modal = page.locator('#tagsModal');
    await expect(modal).toBeVisible();
    await expect(modal.locator('#tagsModalTitle')).toHaveText('Edit Tags');

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

    // Click Save Changes; the tags are saved via PUT and the page reloads
    const saveButton = page.locator('#tagsModal button[type="submit"]').filter({ hasText: 'Save Changes' });
    await expect(saveButton).toBeVisible();
    const [response] = await Promise.all([
      page.waitForResponse(resp => resp.url().includes(`/api/media/${PHOTO_ID}/tags`)),
      saveButton.click(),
    ]);
    expect(response.ok()).toBeTruthy();

    // After the reload the new tag appears in the Tags section on the page
    const newTag = tagsSection.locator('.tag-item').filter({ hasText: TEST_TAG_NAME });
    await expect(newTag.first()).toBeVisible({ timeout: 10000 });

    // Verify tag displays both name and value
    await expect(newTag.first().locator('.tag-name')).toHaveText(TEST_TAG_NAME);
    await expect(newTag.first().locator('.tag-value')).toHaveText(TEST_TAG_VALUE);

    // Note: Cleanup restores the original tag set in test.afterEach via the API
  });
});
