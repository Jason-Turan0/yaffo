/**
 * Generated from: specs/photo_gallery.yaml
 * Feature: photo_gallery
 * Generated at: 2025-12-25T00:00:00Z
 *
 * This test verifies the photo gallery loads correctly and all images are valid.
 */

import { test, expect } from '@playwright/test';

test.describe('Photo Gallery', () => {
  test.describe.configure({ mode: 'serial' });

  test('gallery_loads_with_valid_images', async ({ page, request }) => {
    // Step 1: Navigate to the home page
    await page.goto('/');

    // Step 2: Wait for the gallery grid to be visible
    const galleryGrid = page.locator('.photo-grid');
    await expect(galleryGrid).toBeVisible({ timeout: 10000 });

    // Step 3: Collect all image elements in the gallery
    const photoCards = page.locator('.photo-card img');
    const imageCount = await photoCards.count();

    // Verify: At least one photo is displayed
    expect(imageCount, 'Expected at least one photo in the gallery').toBeGreaterThan(0);

    console.log(`Found ${imageCount} images in gallery`);

    // Step 4: Verify each image source returns HTTP 200
    const brokenImages: string[] = [];
    const fallbackImages: string[] = [];

    for (let i = 0; i < imageCount; i++) {
      const img = photoCards.nth(i);
      const src = await img.getAttribute('src');
      const fallbackSrc = await img.getAttribute('data-fallback');

      if (!src) {
        brokenImages.push(`Image ${i + 1}: Missing src attribute`);
        continue;
      }

      // Build absolute URL
      const absoluteUrl = new URL(src, page.url()).href;

      // Check if image URL returns 200
      const response = await request.get(absoluteUrl);

      if (response.status() !== 200) {
        brokenImages.push(`Image ${i + 1}: ${src} returned ${response.status()}`);
      }

      // Check if image is using fallback (would indicate original failed to load)
      // Compare current src to data-fallback - if they match, the original image failed
      const currentSrc = await img.evaluate((el: HTMLImageElement) => el.src);
      if (fallbackSrc) {
        const fallbackUrl = new URL(fallbackSrc, page.url()).href;
        const currentUrl = new URL(currentSrc, page.url()).href;
        if (currentUrl === fallbackUrl) {
          fallbackImages.push(`Image ${i + 1}: Using fallback (original src failed)`);
        }
      }
    }

    // Verify: All photo image sources return HTTP 200 (no broken links)
    if (brokenImages.length > 0) {
      console.error('Broken images found:');
      brokenImages.forEach(msg => console.error(`  - ${msg}`));
    }
    expect(brokenImages, `Found ${brokenImages.length} broken image(s)`).toHaveLength(0);

    // Verify: No images are using the fallback placeholder
    if (fallbackImages.length > 0) {
      console.warn('Images using fallback:');
      fallbackImages.forEach(msg => console.warn(`  - ${msg}`));
    }
    expect(fallbackImages, `Found ${fallbackImages.length} image(s) using fallback`).toHaveLength(0);

    console.log(`All ${imageCount} images verified successfully`);
  });
});