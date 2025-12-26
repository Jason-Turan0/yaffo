import { test, expect } from '@playwright/test';

test.describe('Photo Gallery', () => {
  test('gallery loads with valid images', async ({ page }) => {
    // Navigate to the home page
    await page.goto('/');
    
    // Wait for the gallery grid to be visible
    await expect(page.locator('.photo-grid')).toBeVisible({ timeout: 10000 });
    
    // Collect all image elements in the gallery
    const images = await page.locator('.photo-card img').all();
    
    // Verify at least one photo is displayed
    expect(images.length).toBeGreaterThan(0);
    
    // Check each image source for HTTP 200 and ensure it's not using fallback
    for (const image of images) {
      const src = await image.getAttribute('src');
      const fallbackSrc = await image.getAttribute('data-fallback');
      
      // Ensure we have a valid image source
      expect(src).toBeTruthy();
      
      // Verify the image is not using the fallback placeholder
      expect(src).not.toEqual(fallbackSrc);
      
      // Make HTTP request to verify image returns 200
      if (src) {
        const baseUrl = new URL(page.url()).origin;
        const imageUrl = src.startsWith('/') ? `${baseUrl}${src}` : src;
        
        const response = await page.request.get(imageUrl);
        expect(response.status()).toBe(200);
        
        // Verify it's actually an image by checking content-type
        const contentType = response.headers()['content-type'];
        expect(contentType).toMatch(/^image\//i);
      }
    }
    
    // Verify gallery grid container is visible
    await expect(page.locator('.photo-gallery')).toBeVisible();
    
    // Verify no empty state is shown
    await expect(page.locator('.empty-state')).not.toBeVisible();
  });
  
  test('gallery filter year works', async ({ page }) => {
    // Navigate to the home page
    await page.goto('/');
    
    // Wait for the gallery grid to be visible
    await expect(page.locator('.photo-grid')).toBeVisible({ timeout: 10000 });
    
    // Verify at least one year is in the filter dropdown
    const yearSelect = page.locator('#year-select');
    await expect(yearSelect).toBeVisible();
    
    // Get all year options (excluding "All Years")
    const yearOptions = await yearSelect.locator('option:not([value=""])').all();
    expect(yearOptions.length).toBeGreaterThan(0);
    
    // Get the first available year
    const firstYearOption = yearOptions[0];
    const selectedYear = await firstYearOption.getAttribute('value');
    expect(selectedYear).toBeTruthy();
    
    // Count initial photos before filtering
    const initialPhotos = await page.locator('.photo-card').all();
    const initialCount = initialPhotos.length;
    expect(initialCount).toBeGreaterThan(0);
    
    // Select a year from the Year filter
    await yearSelect.selectOption(selectedYear!);
    
    // Click Apply Filters
    await page.locator('button.filter-btn').click();
    
    // Wait for page to reload/update
    await page.waitForLoadState('networkidle');
    
    // Verify gallery grid is still visible after filtering
    await expect(page.locator('.photo-grid')).toBeVisible();
    
    // Verify URL contains the year parameter
    const currentUrl = page.url();
    expect(currentUrl).toContain(`year=${selectedYear}`);
    
    // Get all photo cards after filtering
    const filteredPhotos = await page.locator('.photo-card').all();
    
    // Verify we still have photos (at least one should match the year)
    expect(filteredPhotos.length).toBeGreaterThan(0);
    
    // Verify all displayed photos are from the selected year
    for (const photoCard of filteredPhotos) {
      const photoDate = await photoCard.locator('.photo-date').textContent();
      expect(photoDate).toBeTruthy();
      
      // Extract year from date string (assuming format like "January 1, 2023")
      if (photoDate && !photoDate.includes('Unknown date')) {
        expect(photoDate).toContain(selectedYear!);
      }
    }
    
    // Click Clear Filters
    await page.locator('button.clear-filters').click();
    
    // Wait for page to reload
    await page.waitForLoadState('networkidle');
    
    // Verify URL no longer contains year parameter
    const clearedUrl = page.url();
    expect(clearedUrl).not.toContain('year=');
    
    // Verify gallery grid is still visible
    await expect(page.locator('.photo-grid')).toBeVisible();
    
    // Verify year select is back to "All Years"
    const selectedValue = await yearSelect.inputValue();
    expect(selectedValue).toBe('');
    
    // Verify all photos are shown again (should be >= initial count)
    const finalPhotos = await page.locator('.photo-card').all();
    expect(finalPhotos.length).toBeGreaterThanOrEqual(initialCount);
  });
});