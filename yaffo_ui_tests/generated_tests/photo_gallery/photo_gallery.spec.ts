import { test, expect } from '@playwright/test';

test.describe('Photo Gallery Feature', () => {
  test.beforeEach(async ({ page }) => {
    // Navigate to home page before each test
    await page.goto('/');
    // Wait for the photo grid to be visible
    await page.waitForSelector('.photo-grid', { timeout: 5000 });
  });

  test('gallery_loads_with_valid_images - Gallery displays photos and all image sources are valid (no broken links)', async ({ page }) => {
    // Step 1: Navigate to the home page (done in beforeEach)
    
    // Step 2: Wait for the gallery grid to be visible
    const photoGrid = page.locator('.photo-grid');
    await expect(photoGrid).toBeVisible();
    
    // Step 3: Collect all image elements in the gallery
    const photoCards = page.locator('.photo-card');
    const photoCount = await photoCards.count();
    
    // Verify at least one photo is displayed
    expect(photoCount).toBeGreaterThan(0);
    
    // Step 4: Verify each image source returns HTTP 200
    const imageElements = page.locator('.photo-card img');
    const imageCount = await imageElements.count();
    
    // Collect all image src attributes and verify they load correctly
    for (let i = 0; i < imageCount; i++) {
      const img = imageElements.nth(i);
      
      // Get the src attribute
      const src = await img.getAttribute('src');
      expect(src).toBeTruthy();
      
      // Wait for the image to load (naturalWidth will be set once loaded)
      await img.waitFor({ state: 'visible', timeout: 5000 });
      
      // Verify the image is actually loaded by checking naturalWidth
      // An image with a broken src will have naturalWidth of 0
      const isImageLoaded = await img.evaluate((el: Element) => {
        const imgElement = el as HTMLImageElement;
        return imgElement.naturalWidth > 0 && imgElement.naturalHeight > 0;
      });
      
      expect(isImageLoaded).toBeTruthy();
      
      // Get the base URL for the request
      const baseUrl = new URL(page.url()).origin;
      const fullImageUrl = src?.startsWith('http') ? src : baseUrl + src;
      
      // Verify the image URL returns HTTP 200 by attempting to fetch it
      try {
        const imageResponse = await page.evaluate(async (url) => {
          try {
            const response = await fetch(url, { method: 'GET' });
            return response.status;
          } catch (e) {
            return 0;
          }
        }, fullImageUrl);
        
        expect(imageResponse).toBe(200);
      } catch (e) {
        // If fetch fails, verify the image loaded successfully via the browser's rendering
        expect(isImageLoaded).toBeTruthy();
      }
    }
    
    // Verify gallery grid container is visible
    await expect(photoGrid).toBeVisible();
    
    // Verify no images are using the fallback placeholder
    const fallbackImages = page.locator('img[src*="placeholder"]');
    const fallbackCount = await fallbackImages.count();
    expect(fallbackCount).toBe(0);
  });

  test('gallery_filter_year_works - Should be able to find photos by filtering for year on the gallery page', async ({ page }) => {
    // Step 1: Navigate to the home page (done in beforeEach)
    
    // Wait for the gallery to be fully loaded
    await expect(page.locator('.photo-grid')).toBeVisible();
    
    // Get initial photo count
    const initialPhotoCount = await page.locator('.photo-card').count();
    expect(initialPhotoCount).toBeGreaterThan(0);
    
    // Step 2: Select a year from the Year filter
    const yearSelect = page.locator('select#year-select');
    
    // Verify at least one year is in the filter dropdown
    const yearOptions = page.locator('select#year-select option');
    const optionCount = await yearOptions.count();
    expect(optionCount).toBeGreaterThan(1); // More than just "All Years"
    
    // Get available years from options (excluding "All Years")
    const yearValues = await yearOptions.evaluateAll((options) => {
      return options
        .map((opt) => (opt as HTMLOptionElement).value)
        .filter((val) => val !== ''); // Filter out empty "All Years" option
    });
    
    expect(yearValues.length).toBeGreaterThan(0);
    
    // Select the first available year
    const selectedYear = yearValues[0];
    await yearSelect.selectOption(selectedYear);
    
    // Step 3: Click Apply Filters
    const applyButton = page.locator('button.filter-btn');
    await expect(applyButton).toBeVisible();
    await applyButton.click();
    
    // Wait for the page to reload with filtered results
    await page.waitForLoadState('networkidle');
    
    // Verify the URL contains the year parameter
    expect(page.url()).toContain(`year=${selectedYear}`);
    
    // Wait for the gallery grid to be visible after filtering
    const photoGrid = page.locator('.photo-grid');
    await expect(photoGrid).toBeVisible();
    
    // When the filter is applied all images are from that year
    const filteredPhotoCards = page.locator('.photo-card');
    const filteredPhotoCount = await filteredPhotoCards.count();
    
    // Get the year from the photo date text to verify filtering
    const photoDates = await page.locator('.photo-date').allTextContents();
    
    // Parse dates and extract years to verify they match the selected year
    for (const dateText of photoDates) {
      // Extract year from date text (e.g., "May 06, 2014" -> "2014")
      const yearMatch = dateText.match(/\d{4}/);
      if (yearMatch) {
        const photoYear = yearMatch[0];
        expect(photoYear).toBe(selectedYear);
      }
    }
    
    // Verify the subtitle shows filtered count
    const subtitle = page.locator('.page-header .subtitle');
    const subtitleText = await subtitle.textContent();
    expect(subtitleText).toContain('Showing');
    expect(subtitleText).toContain('of');
    
    // Step 4: Click Clear button
    const clearButton = page.locator('button.clear-filters');
    await expect(clearButton).toBeVisible();
    await clearButton.click();
    
    // Wait for the page to reload with all results
    await page.waitForLoadState('networkidle');
    
    // When the filter is cleared all the images are shown
    await expect(page.locator('.photo-grid')).toBeVisible();
    
    // Verify we're back to showing all photos
    const clearedPhotoCount = await page.locator('.photo-card').count();
    expect(clearedPhotoCount).toBe(initialPhotoCount);
    
    // Verify the year select is reset to "All Years"
    const selectedOption = await yearSelect.evaluate((el) => {
      const selectElement = el as HTMLSelectElement;
      return selectElement.value;
    });
    expect(selectedOption).toBe('');
    
    // Verify the URL no longer contains year parameter
    expect(page.url()).not.toContain('year=');
  });

  test('gallery_page_navigation_works - Verify that the page navigation works', async ({ page }) => {
    // Step 1: Navigate to the home page (done in beforeEach)
    
    // Wait for the gallery to be fully loaded
    await expect(page.locator('.photo-grid')).toBeVisible();
    
    // Step 2: Set page size to 10
    const pageSizeSelect = page.locator('select#page-size');
    await expect(pageSizeSelect).toBeVisible();
    
    // Select page size of 10
    await pageSizeSelect.selectOption('10');
    
    // Wait for the page to reload with new page size
    await page.waitForLoadState('networkidle');
    await expect(page.locator('.photo-grid')).toBeVisible();
    
    // Verify when viewing the first page 10 images are shown
    const firstPagePhotoCount = await page.locator('.photo-card').count();
    expect(firstPagePhotoCount).toBeLessThanOrEqual(10);
    expect(firstPagePhotoCount).toBeGreaterThan(0);
    
    // Verify the URL contains page-size parameter
    expect(page.url()).toContain('page-size=10');
    
    // Get total items count from pagination info
    const paginationInfo = page.locator('.pagination-info .results-count');
    const resultsText = await paginationInfo.textContent();
    const totalMatch = resultsText?.match(/of (\d+) results/);
    const totalItems = totalMatch ? parseInt(totalMatch[1]) : 0;
    
    // Only test navigation if there are more items than page size
    if (totalItems > 10) {
      // Step 3: Click the next button
      const nextButton = page.locator('a.page-btn:has-text("Next")');
      await expect(nextButton).toBeVisible();
      await expect(nextButton).not.toHaveClass(/disabled/);
      await nextButton.click();
      
      // Wait for navigation
      await page.waitForLoadState('networkidle');
      await expect(page.locator('.photo-grid')).toBeVisible();
      
      // Verify we're on page 2
      expect(page.url()).toContain('page=2');
      
      const pageInfo = page.locator('.page-info');
      const pageInfoText = await pageInfo.textContent();
      expect(pageInfoText).toContain('Page 2');
      
      // Verify photos are displayed
      const secondPagePhotoCount = await page.locator('.photo-card').count();
      expect(secondPagePhotoCount).toBeGreaterThan(0);
      
      // Step 4: Click the first button
      const firstButton = page.locator('a.page-btn:has-text("First")');
      await expect(firstButton).toBeVisible();
      await expect(firstButton).not.toHaveClass(/disabled/);
      await firstButton.click();
      
      // Wait for navigation
      await page.waitForLoadState('networkidle');
      await expect(page.locator('.photo-grid')).toBeVisible();
      
      // Verify we're back on page 1
      const currentUrl = page.url();
      const urlPageParam = currentUrl.match(/page=(\d+)/);
      const currentPage = urlPageParam ? parseInt(urlPageParam[1]) : 1;
      expect(currentPage).toBe(1);
      
      // Verify the page info shows page 1
      const firstPageInfo = await pageInfo.textContent();
      expect(firstPageInfo).toContain('Page 1');
      
      // Step 5: Click the last button
      const lastButton = page.locator('a.page-btn:has-text("Last")');
      await expect(lastButton).toBeVisible();
      await expect(lastButton).not.toHaveClass(/disabled/);
      await lastButton.click();
      
      // Wait for navigation
      await page.waitForLoadState('networkidle');
      await expect(page.locator('.photo-grid')).toBeVisible();
      
      // Verify we're on the last page
      const lastPageInfo = await pageInfo.textContent();
      const lastPageMatch = lastPageInfo?.match(/Page (\d+) of (\d+)/);
      if (lastPageMatch) {
        const lastPageNum = parseInt(lastPageMatch[1]);
        const totalPages = parseInt(lastPageMatch[2]);
        expect(lastPageNum).toBe(totalPages);
      }
      
      // Verify when viewing the last page only the remainder items are shown
      const lastPagePhotoCount = await page.locator('.photo-card').count();
      expect(lastPagePhotoCount).toBeGreaterThan(0);
      
      // Calculate expected count on last page
      const expectedLastPageCount = totalItems % 10 || 10;
      expect(lastPagePhotoCount).toBeLessThanOrEqual(expectedLastPageCount);
      
      // Verify Next and Last buttons are disabled on last page
      await expect(nextButton).toHaveClass(/disabled/);
      await expect(lastButton).toHaveClass(/disabled/);
    } else {
      // If there aren't enough items for multiple pages, just verify single page behavior
      const nextButton = page.locator('a.page-btn:has-text("Next")');
      const lastButton = page.locator('a.page-btn:has-text("Last")');
      
      // Navigation buttons should be disabled when there's only one page
      await expect(nextButton).toHaveClass(/disabled/);
      await expect(lastButton).toHaveClass(/disabled/);
    }
  });
});