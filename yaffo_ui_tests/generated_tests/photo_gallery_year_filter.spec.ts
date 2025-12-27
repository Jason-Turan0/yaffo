import { test, expect } from '@playwright/test';

test.describe('Photo Gallery - Year Filter', () => {
  test('gallery filter year works', async ({ page }) => {
    // Navigate to the home page
    await page.goto('/');

    // Wait for the gallery grid to be visible
    await expect(page.locator('.photo-grid')).toBeVisible();

    // Verify gallery grid container is visible
    await expect(page.locator('.photo-grid')).toBeVisible();

    // Get initial photo count
    const initialSubtitle = page.locator('.subtitle');
    await expect(initialSubtitle).toBeVisible();
    const initialText = await initialSubtitle.textContent();
    const initialCount = parseInt(initialText?.match(/Showing (\d+) of/)?.[1] || '0');
    expect(initialCount).toBeGreaterThan(0);

    // Verify at least one year is in the filter dropdown
    const yearSelect = page.locator('#year-select');
    await expect(yearSelect).toBeVisible();
    
    // Get available years from the dropdown - exclude the "All Years" option which has empty value
    const yearOptions = await page.locator('#year-select option').evaluateAll((options) => {
      return options
        .filter((option: HTMLOptionElement) => option.value && option.value.trim() !== '')
        .map((option: HTMLOptionElement) => ({
          value: option.value,
          text: option.textContent?.trim() || ''
        }));
    });
    
    expect(yearOptions.length).toBeGreaterThan(0);

    // Select the first available year (not "All Years")
    const firstYear = yearOptions[0];
    
    await yearSelect.selectOption(firstYear.value);

    // Click Apply Filters
    const applyButton = page.getByRole('button', { name: 'Apply Filters' });
    await expect(applyButton).toBeVisible();
    await applyButton.click();

    // Wait for the page to update after filtering
    await page.waitForLoadState('networkidle');
    
    // Verify URL contains the year parameter
    expect(page.url()).toContain(`year=${firstYear.value}`);

    // Verify gallery grid is still visible
    await expect(page.locator('.photo-grid')).toBeVisible();

    // Get filtered photo count
    await expect(initialSubtitle).toBeVisible();
    const filteredText = await initialSubtitle.textContent();
    const filteredCount = parseInt(filteredText?.match(/Showing (\d+) of/)?.[1] || '0');
    
    // Verify we have at least one photo with the filter applied
    expect(filteredCount).toBeGreaterThan(0);

    // When the filter is applied, verify all images are from that year
    // by checking that all photo cards show the correct year in their date
    const photoDates = page.locator('.photo-date');
    const dateCount = await photoDates.count();
    
    if (dateCount > 0) {
      for (let i = 0; i < dateCount; i++) {
        const dateText = await photoDates.nth(i).textContent();
        if (dateText && !dateText.includes('Unknown date')) {
          // Check that the date contains the selected year
          expect(dateText).toContain(firstYear.text);
        }
      }
    }

    // Click Clear button
    const clearButton = page.getByRole('button', { name: 'Clear Filters' });
    await expect(clearButton).toBeVisible();
    await clearButton.click();

    // Wait for the page to update after clearing
    await page.waitForLoadState('networkidle');

    // Verify URL no longer contains year parameter
    expect(page.url()).not.toContain(`year=${firstYear.value}`);

    // Verify gallery grid is still visible
    await expect(page.locator('.photo-grid')).toBeVisible();

    // When the filter is cleared, verify all images are shown
    await expect(initialSubtitle).toBeVisible();
    const clearedText = await initialSubtitle.textContent();
    const clearedCount = parseInt(clearedText?.match(/Showing (\d+) of/)?.[1] || '0');
    
    // The cleared count should be equal to or greater than the filtered count
    expect(clearedCount).toBeGreaterThanOrEqual(filteredCount);
    
    // Verify year dropdown is reset to "All Years"
    const selectedOption = await yearSelect.inputValue();
    expect(selectedOption).toBe('');
  });

  test('year filter shows correct results for different years', async ({ page }) => {
    await page.goto('/');
    
    // Wait for gallery to load
    await expect(page.locator('.photo-grid')).toBeVisible();
    
    const yearSelect = page.locator('#year-select');
    const applyButton = page.getByRole('button', { name: 'Apply Filters' });
    
    // Get available years from the dropdown - exclude the "All Years" option
    const yearOptions = await page.locator('#year-select option').evaluateAll((options) => {
      return options
        .filter((option: HTMLOptionElement) => option.value && option.value.trim() !== '')
        .map((option: HTMLOptionElement) => ({
          value: option.value,
          text: option.textContent?.trim() || ''
        }));
    });
    
    if (yearOptions.length > 1) {
      // Test multiple years to ensure filtering works correctly
      const yearResults: { year: string, count: number }[] = [];
      
      for (let i = 0; i < Math.min(yearOptions.length, 2); i++) {
        const yearOption = yearOptions[i];
        
        // Select the year
        await yearSelect.selectOption(yearOption.value);
        await applyButton.click();
        await page.waitForLoadState('networkidle');
        
        // Get result count
        const subtitle = page.locator('.subtitle');
        const subtitleText = await subtitle.textContent();
        const count = parseInt(subtitleText?.match(/Showing (\d+) of/)?.[1] || '0');
        
        yearResults.push({ year: yearOption.text, count });
        
        // Verify all photos are from the selected year
        const photoDates = page.locator('.photo-date');
        const dateCount = await photoDates.count();
        
        for (let j = 0; j < dateCount; j++) {
          const dateText = await photoDates.nth(j).textContent();
          if (dateText && !dateText.includes('Unknown date')) {
            expect(dateText).toContain(yearOption.text);
          }
        }
      }
      
      // Results should be different for different years (unless all photos are from same year)
      if (yearResults.length > 1 && yearResults[0].year !== yearResults[1].year) {
        // At least verify that we got some results for each year tested
        yearResults.forEach(result => {
          expect(result.count).toBeGreaterThan(0);
        });
      }
    } else {
      // If only one year option available, just verify basic filtering works
      const yearOption = yearOptions[0];
      await yearSelect.selectOption(yearOption.value);
      await applyButton.click();
      await page.waitForLoadState('networkidle');
      
      const subtitle = page.locator('.subtitle');
      const subtitleText = await subtitle.textContent();
      const count = parseInt(subtitleText?.match(/Showing (\d+) of/)?.[1] || '0');
      expect(count).toBeGreaterThan(0);
    }
  });
});