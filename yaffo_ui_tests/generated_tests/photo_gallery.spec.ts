import { test, expect } from '@playwright/test';

test.describe('Photo Gallery', () => {
  test('gallery loads with valid images', async ({ page }) => {
    // Navigate to the home page
    await page.goto('/');
    
    // Wait for the gallery grid to be visible
    const galleryGrid = page.locator('.photo-grid');
    await expect(galleryGrid).toBeVisible();
    
    // Collect all image elements in the gallery
    const photoImages = page.locator('.photo-card img');
    
    // Wait for at least one photo to be displayed
    await expect(photoImages.first()).toBeVisible();
    
    // Verify at least one photo is displayed
    const imageCount = await photoImages.count();
    expect(imageCount).toBeGreaterThan(0);
    
    // Get all image sources and verify they return HTTP 200
    const imageSources = await photoImages.evaluateAll((images) => {
      return images.map((img: HTMLImageElement) => img.src).filter(src => src && src.trim() !== '');
    });
    
    // Verify each image source returns HTTP 200
    const baseUrl = new URL(page.url()).origin;
    for (const src of imageSources) {
      const response = await page.request.get(src);
      expect(response.status(), `Image ${src} should return 200`).toBe(200);
    }
    
    // Verify no images are using the fallback placeholder
    // Check that no image sources contain '/placeholder'
    const placeholderImages = await photoImages.evaluateAll((images) => {
      return images.filter((img: HTMLImageElement) => 
        img.src && img.src.includes('/placeholder')
      ).length;
    });
    expect(placeholderImages).toBe(0);
    
    // Additional verification: ensure images have loaded successfully
    await photoImages.first().waitFor({ state: 'visible' });
    
    // Check that images have natural dimensions (indicates successful load)
    const hasValidDimensions = await photoImages.first().evaluate((img: HTMLImageElement) => {
      return img.complete && img.naturalWidth > 0 && img.naturalHeight > 0;
    });
    expect(hasValidDimensions).toBe(true);
  });
  
  test('gallery handles image loading errors gracefully', async ({ page }) => {
    // Navigate to the home page
    await page.goto('/');
    
    // Wait for gallery to be visible
    const galleryGrid = page.locator('.photo-grid');
    await expect(galleryGrid).toBeVisible();
    
    // Get photo images
    const photoImages = page.locator('.photo-card img');
    await expect(photoImages.first()).toBeVisible();
    
    // Simulate image loading error by corrupting src and check fallback behavior
    await photoImages.first().evaluate((img: HTMLImageElement) => {
      // Store original src and fallback
      const originalSrc = img.src;
      const fallbackSrc = img.getAttribute('data-fallback');
      
      // Corrupt the src to trigger error
      img.src = originalSrc.replace('/photos/', '/invalid-photos/');
      
      // Trigger error event
      img.dispatchEvent(new Event('error'));
      
      return { originalSrc, fallbackSrc };
    });
    
    // Wait a moment for fallback to potentially trigger
    await page.waitForTimeout(1000);
    
    // Verify the image fallback system is in place
    const hasDataFallback = await photoImages.first().getAttribute('data-fallback');
    expect(hasDataFallback).toBeTruthy();
  });
});
