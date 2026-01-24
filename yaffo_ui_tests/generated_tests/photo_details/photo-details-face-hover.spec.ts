import { test, expect } from '@playwright/test';

test.describe('Photo Details - Face Hover Highlights', () => {
  const PHOTO_ID = 7;

  test('photo_details_face_hover_highlights', async ({ page }) => {
    // Navigate to the photo details page
    await page.goto(`/photo/view/${PHOTO_ID}`);

    // Wait for page to load
    await expect(page.locator('h2').filter({ hasText: 'Photo Details' })).toBeVisible();

    // Verify face canvas exists
    const faceCanvas = page.locator('#faceCanvas');
    await expect(faceCanvas).toBeAttached();

    // Find face thumbnails
    const facesGrid = page.locator('.faces-grid');
    const faceThumbnails = facesGrid.locator('.face-thumbnail');
    
    // Check if faces exist
    const faceCount = await faceThumbnails.count();
    if (faceCount === 0) {
      test.skip(true, 'Photo does not have detected faces');
      return;
    }

    // Get the first face thumbnail
    const firstFace = faceThumbnails.first();
    await expect(firstFace).toBeVisible();
    
    // Verify face canvas is initially not highlighted
    // Canvas exists but should be empty/clear before hover
    const canvasInitialState = await faceCanvas.evaluate((canvas: HTMLCanvasElement) => {
      const ctx = canvas.getContext('2d');
      if (!ctx) return null;
      const imageData = ctx.getImageData(0, 0, canvas.width, canvas.height);
      // Check if canvas has any non-transparent pixels
      return imageData.data.some((value, index) => index % 4 === 3 && value > 0);
    });
    // Initially should be empty (false or null)
    expect(canvasInitialState).toBeFalsy();

    // Hover over the face thumbnail
    await firstFace.hover();
    await page.waitForTimeout(150); // Wait for highlight to render

    // Verify the corresponding face region is highlighted on the main photo
    // Check that canvas now has content (face highlight drawn)
    const canvasHighlightedState = await faceCanvas.evaluate((canvas: HTMLCanvasElement) => {
      const ctx = canvas.getContext('2d');
      if (!ctx) return null;
      const imageData = ctx.getImageData(0, 0, canvas.width, canvas.height);
      // Check if canvas has any non-transparent pixels
      return imageData.data.some((value, index) => index % 4 === 3 && value > 0);
    });
    expect(canvasHighlightedState).toBeTruthy();

    // Verify face thumbnail gets highlighted class
    await expect(firstFace).toHaveClass(/highlighted/);

    // Move mouse away to clear the highlight
    await page.mouse.move(0, 0);
    await page.waitForTimeout(150);

    // Verify highlight clears
    const canvasClearedState = await faceCanvas.evaluate((canvas: HTMLCanvasElement) => {
      const ctx = canvas.getContext('2d');
      if (!ctx) return null;
      const imageData = ctx.getImageData(0, 0, canvas.width, canvas.height);
      return imageData.data.some((value, index) => index % 4 === 3 && value > 0);
    });
    expect(canvasClearedState).toBeFalsy();

    // Verify highlighted class is removed
    await expect(firstFace).not.toHaveClass(/highlighted/);
  });
});