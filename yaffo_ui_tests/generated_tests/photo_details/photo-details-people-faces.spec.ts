import { test, expect } from '@playwright/test';

test.describe('Photo Details - People and Faces', () => {
  const PHOTO_ID = 14;

  test('photo_details_displays_people_and_faces', async ({ page }) => {
    // Navigate to the photo details page
    await page.goto(`/photo/view/${PHOTO_ID}`);

    // Wait for the page to load
    await expect(page.locator('h2').filter({ hasText: 'Photo Details' })).toBeVisible();

    // Verify People section shows the count
    const peopleSection = page.locator('.detail-section').filter({ hasText: /People \(\d+\)/ });
    await expect(peopleSection).toBeVisible();
    const peopleHeader = peopleSection.locator('h3');
    const peopleText = await peopleHeader.textContent();
    const peopleCount = peopleText?.match(/People \((\d+)\)/);
    expect(peopleCount).toBeTruthy();

    // Check if people are assigned - if so, verify person links are clickable
    const peopleList = page.locator('.people-list');
    if (await peopleList.isVisible()) {
      const personLinks = peopleList.locator('a.person-tag');
      const count = await personLinks.count();
      expect(count).toBeGreaterThan(0);
      
      // Verify first person link is clickable and has href
      const firstLink = personLinks.first();
      await expect(firstLink).toBeVisible();
      const href = await firstLink.getAttribute('href');
      expect(href).toMatch(/\/person\/\d+\/faces/);
    }

    // Verify Faces section shows the count
    const facesSection = page.locator('.detail-section').filter({ hasText: /Faces \(\d+\)/ });
    await expect(facesSection).toBeVisible();
    const facesHeader = facesSection.locator('h3');
    const facesText = await facesHeader.textContent();
    const facesCount = facesText?.match(/Faces \((\d+)\)/);
    expect(facesCount).toBeTruthy();

    // Check if faces exist - if so, verify thumbnails load correctly
    const facesGrid = page.locator('.faces-grid');
    if (await facesGrid.isVisible()) {
      const faceThumbnails = facesGrid.locator('.face-thumbnail');
      const count = await faceThumbnails.count();
      expect(count).toBeGreaterThan(0);

      // Verify face thumbnails have the correct attributes
      const firstFace = faceThumbnails.first();
      await expect(firstFace).toHaveAttribute('data-face-id');
      
      // Verify face image loads
      const faceImg = firstFace.locator('img');
      await expect(faceImg).toBeVisible();
      const faceSrc = await faceImg.getAttribute('src');
      expect(faceSrc).toMatch(/\/faces\/\d+/);

      // Test hovering over a face thumbnail highlights it on the main image
      const faceCanvas = page.locator('#faceCanvas');
      await expect(faceCanvas).toBeAttached();
      
      // Hover over the first face thumbnail
      await firstFace.hover();
      await page.waitForTimeout(100); // Brief wait for highlight to render
      
      // Verify the face thumbnail gets highlighted class
      await expect(firstFace).toHaveClass(/highlighted/);
    }
  });
});