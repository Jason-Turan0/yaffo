import { test, expect } from '@playwright/test';

test.describe('Photo Gallery - Image Loading', () => {
  test('gallery loads with valid images', async ({ page }) => {
    // Navigate to the home page
    await page.goto('/');

    // Wait for the gallery grid to be visible
    await expect(page.locator('.photo-grid')).toBeVisible();

    // Verify gallery grid container is visible
    const galleryGrid = page.locator('.photo-grid');
    await expect(galleryGrid).toBeVisible();

    // Wait for photos to load and collect all image elements in the gallery
    await page.waitForSelector('.photo-card img', { timeout: 10000 });
    const images = page.locator('.photo-card img');

    // Verify at least one photo is displayed
    const imageCount = await images.count();
    expect(imageCount).toBeGreaterThan(0);

    // Verify each image source returns HTTP 200
    for (let i = 0; i < imageCount; i++) {
      const image = images.nth(i);
      const imageSrc = await image.getAttribute('src');
      
      if (imageSrc) {
        // Make a HEAD request to check if the image URL is valid
        const baseUrl = new URL(page.url()).origin;
        const fullImageUrl = imageSrc.startsWith('http') ? imageSrc : `${baseUrl}${imageSrc}`;
        
        const response = await page.request.head(fullImageUrl);
        expect(response.status()).toBe(200);
        
        // Verify the image is not using the fallback placeholder
        const dataFallback = await image.getAttribute('data-fallback');
        expect(imageSrc).not.toBe(dataFallback);
        
        // Additional check: verify image has loaded by checking naturalWidth
        const naturalWidth = await image.evaluate((img: HTMLImageElement) => img.naturalWidth);
        expect(naturalWidth).toBeGreaterThan(0);
      }
    }

    // Verify no images are using the fallback placeholder by checking if any images have src equal to data-fallback
    const placeholderImages = await page.locator('.photo-card img').evaluateAll((images) => {
      return images.filter((img: HTMLImageElement) => {
        const src = img.src;
        const fallback = img.getAttribute('data-fallback');
        return fallback && src.endsWith(fallback);
      }).length;
    });
    expect(placeholderImages).toBe(0);

    // Verify the photo count is displayed correctly
    const subtitle = page.locator('.subtitle');
    await expect(subtitle).toBeVisible();
    const subtitleText = await subtitle.textContent();
    expect(subtitleText).toMatch(/Showing \d+ of \d+ photos?/);
  });

  test('gallery handles broken images gracefully', async ({ page }) => {
    await page.goto('/');
    
    // Wait for the gallery to load
    await expect(page.locator('.photo-grid')).toBeVisible();
    
    // Test error handling by intercepting image requests and making some fail
    await page.route('**/photos/1', route => route.fulfill({ status: 404 }));
    
    // Reload to trigger the error
    await page.reload();
    
    // Wait for gallery to load
    await expect(page.locator('.photo-grid')).toBeVisible();
    
    // The gallery should still be functional even with broken images
    const images = page.locator('.photo-card img');
    const imageCount = await images.count();
    expect(imageCount).toBeGreaterThan(0);
  });
});