// Generated from specs/photo_gallery.yaml
// Feature: photo_gallery - User can browse photos in the gallery and all images load correctly
// Base URL: http://127.0.0.1:5000
// Generated at: 2025-12-25T19:22:03.455Z

import { test, expect } from '@playwright/test';

test.describe('Photo Gallery', () => {
  test.beforeEach(async ({ page }) => {
    // Navigate to home page (uses baseURL from playwright.config.ts)
    await page.goto('/');
  });

  test('gallery loads with valid images', async ({ page }) => {
    // Step: Navigate to the home page (already done in beforeEach)
    
    // Step: Wait for the gallery grid to be visible
    const galleryGrid = page.locator('.photo-grid').first();
    await galleryGrid.waitFor({ state: 'visible', timeout: 10000 });
    
    // Verify: Gallery grid container is visible
    await expect(galleryGrid).toBeVisible();
    
    // Step: Collect all image elements in the gallery
    const photoCards = page.locator('.photo-grid .photo-card');
    const imageElements = page.locator('.photo-grid .photo-card img');
    
    // Wait for at least one photo card to be present
    await photoCards.first().waitFor({ state: 'visible', timeout: 10000 });
    
    // Verify: At least one photo is displayed
    const photoCount = await photoCards.count();
    expect(photoCount).toBeGreaterThan(0);
    
    // Step: Verify each image source returns HTTP 200
    const imageCount = await imageElements.count();
    expect(imageCount).toBeGreaterThan(0);
    
    // Check each image source for valid HTTP response
    for (let i = 0; i < imageCount; i++) {
      const img = imageElements.nth(i);
      
      // Wait for the image to be visible
      await img.waitFor({ state: 'visible', timeout: 5000 });
      
      // Get the image source URL
      const imgSrc = await img.getAttribute('src');
      expect(imgSrc).toBeTruthy();
      expect(imgSrc).not.toBeNull();
      
      // Verify: No images are using the fallback placeholder
      // Common placeholder patterns to check against
      const placeholderPatterns = [
        'placeholder',
        'fallback',
        'default',
        'missing',
        'broken'
      ];
      
      const srcLower = imgSrc!.toLowerCase();
      for (const pattern of placeholderPatterns) {
        expect(srcLower).not.toContain(pattern);
      }
      
      // Verify: All photo image sources return HTTP 200 (no broken links)
      // Use page.url() to get the base URL dynamically
      const baseUrl = new URL(page.url()).origin;
      const fullUrl = imgSrc!.startsWith('/') ? `${baseUrl}${imgSrc}` : imgSrc!;

      // Use Playwright's request context to check image URL
      const response = await page.request.get(fullUrl);
      expect(response.status()).toBe(200);
      
      // Verify the response contains image content
      const contentType = response.headers()['content-type'];
      expect(contentType).toMatch(/^image\//);
      
      // Ensure image has actually loaded in the browser
      const isImageLoaded = await img.evaluate((element: HTMLImageElement) => {
        return element.complete && element.naturalHeight > 0;
      });
      expect(isImageLoaded).toBe(true);
    }
    
    // Additional verification: Check that images are properly sized and displayed
    for (let i = 0; i < Math.min(imageCount, 5); i++) { // Check first 5 images for performance
      const img = imageElements.nth(i);
      const boundingBox = await img.boundingBox();
      expect(boundingBox).toBeTruthy();
      expect(boundingBox!.width).toBeGreaterThan(0);
      expect(boundingBox!.height).toBeGreaterThan(0);
    }
  });
});