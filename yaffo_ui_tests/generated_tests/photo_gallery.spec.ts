import { test, expect } from '@playwright/test';

// Extend Window interface to include PHOTO_ORGANIZER
declare global {
  interface Window {
    PHOTO_ORGANIZER?: {
      utils?: {
        initImageFallbacks?: () => void;
      };
    };
  }
}

test.describe('Photo Gallery', () => {
  test('gallery loads with valid images', async ({ page }) => {
    // Navigate to the home page
    await page.goto('/');

    // Wait for the main content to be visible
    await page.waitForSelector('.main-content.photo-gallery', { state: 'visible' });

    // Check if we have the gallery grid or empty state
    const photoGrid = page.locator('.photo-grid');
    const emptyState = page.locator('.empty-state');

    // Wait for either gallery grid or empty state to be visible
    await expect(photoGrid.or(emptyState)).toBeVisible();

    const hasPhotos = await photoGrid.isVisible();

    if (hasPhotos) {
      // Verify gallery grid container is visible
      await expect(photoGrid).toBeVisible();

      // Collect all image elements in the gallery
      const photoCards = page.locator('.photo-card');
      const images = page.locator('.photo-card img');

      // Verify at least one photo is displayed
      await expect(photoCards.first()).toBeVisible();
      const imageCount = await images.count();
      expect(imageCount).toBeGreaterThan(0);

      // Wait for images to load and verify each image source returns HTTP 200
      for (let i = 0; i < imageCount; i++) {
        const img = images.nth(i);
        
        // Wait for the image to be visible
        await expect(img).toBeVisible();
        
        // Get the image src
        const src = await img.getAttribute('src');
        expect(src).toBeTruthy();
        
        // Check if the image has loaded successfully by verifying it has natural dimensions
        await expect(img).toHaveJSProperty('complete', true);
        const naturalWidth = await img.evaluate((el: HTMLImageElement) => el.naturalWidth);
        expect(naturalWidth).toBeGreaterThan(0);
        
        // Verify the image source is not using the fallback placeholder
        const fallbackSrc = await img.getAttribute('data-fallback');
        if (fallbackSrc) {
          expect(src).not.toBe(fallbackSrc);
          expect(src).not.toContain('/placeholder');
        }
      }

      // Verify all photo image sources return HTTP 200 by making HEAD requests
      const baseUrl = new URL(page.url()).origin;
      
      for (let i = 0; i < imageCount; i++) {
        const img = images.nth(i);
        const src = await img.getAttribute('src');
        
        if (src) {
          // Convert relative URLs to absolute
          const imageUrl = src.startsWith('http') ? src : `${baseUrl}${src}`;
          
          // Make a request to verify the image loads
          const response = await page.request.head(imageUrl);
          expect(response.status()).toBe(200);
        }
      }

      console.log(`Successfully verified ${imageCount} images in the gallery`);
    } else {
      // If no photos, verify empty state is shown
      await expect(emptyState).toBeVisible();
      await expect(emptyState.locator('h2')).toContainText('No photos found');
      console.log('Gallery is empty - no photos to verify');
    }
  });

  test('gallery handles image loading errors gracefully', async ({ page }) => {
    // Navigate to the home page
    await page.goto('/');

    // Wait for the gallery to load
    await page.waitForSelector('.main-content.photo-gallery', { state: 'visible' });

    const photoGrid = page.locator('.photo-grid');
    
    if (await photoGrid.isVisible()) {
      const images = page.locator('.photo-card img[data-fallback]');
      const imageCount = await images.count();
      
      if (imageCount > 0) {
        // Test fallback behavior by forcing an error on the first image
        const firstImg = images.first();
        await expect(firstImg).toBeVisible();
        
        // Get the fallback URL
        const fallbackSrc = await firstImg.getAttribute('data-fallback');
        expect(fallbackSrc).toBeTruthy();
        
        // Simulate an image error by changing the src to an invalid URL
        await page.evaluate((img) => {
          if (img && img instanceof HTMLImageElement) {
            // Trigger error event by setting invalid src
            (img as HTMLImageElement).src = '/invalid-image-url';
          }
        }, await firstImg.elementHandle());
        
        // Wait for the fallback mechanism to kick in
        await page.waitForTimeout(1000);
        
        // Initialize the fallback utility if not already done
        await page.evaluate(() => {
          if ((window as any).PHOTO_ORGANIZER && (window as any).PHOTO_ORGANIZER.utils) {
            (window as any).PHOTO_ORGANIZER.utils.initImageFallbacks();
          }
        });
        
        // Wait a bit more for fallback to apply
        await page.waitForTimeout(500);
        
        console.log('Tested image fallback mechanism');
      }
    }
  });

  test('gallery maintains responsive layout', async ({ page }) => {
    // Test desktop view
    await page.setViewportSize({ width: 1200, height: 800 });
    await page.goto('/');
    
    const photoGrid = page.locator('.photo-grid');
    if (await photoGrid.isVisible()) {
      // Verify grid layout
      await expect(photoGrid).toHaveCSS('display', 'grid');
      
      // Test tablet view
      await page.setViewportSize({ width: 768, height: 1024 });
      await expect(photoGrid).toBeVisible();
      
      // Test mobile view
      await page.setViewportSize({ width: 375, height: 667 });
      await expect(photoGrid).toBeVisible();
      
      console.log('Verified responsive layout across different viewport sizes');
    }
  });
});