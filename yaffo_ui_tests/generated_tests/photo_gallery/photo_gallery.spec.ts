import { test, expect, Page } from '@playwright/test';

// Helper function to get the number of visible photos
async function getPhotoCount(page: Page): Promise<number> {
  return page.locator('.photo-card').count();
}

// Selects an option in the custom searchable-select widget that wraps a native
// <select> (the native element is hidden, so locator.selectOption won't work).
async function pickSearchableOption(page: Page, selectSelector: string, optionText: string): Promise<void> {
  const wrapper = page.locator(`${selectSelector} + .searchable-select-wrapper`);
  await wrapper.locator('.searchable-select-display').click();
  await wrapper.locator('.searchable-select-option').filter({ hasText: optionText }).first().click();
}

test.describe('Photo Gallery Feature', () => {

  test.beforeEach(async ({ page }) => {
    await page.goto('/');
    await expect(page.locator('.photo-grid')).toBeVisible();
    await expect(page.locator('.photo-card').first()).toBeVisible();
  });

  test('gallery_loads_with_valid_images - Gallery displays photos and all image sources are valid (no broken links)', async ({ page }) => {
    const photoCount = await page.locator('.photo-card').count();
    expect(photoCount).toBeGreaterThan(0);

    const imageLocators = await page.locator('.photo-grid .photo-card img').all();
    expect(imageLocators.length).toBeGreaterThan(0);

    const fallbackSrc = await imageLocators[0].getAttribute('data-fallback');
    expect(fallbackSrc).not.toBeNull();

    for (const img of imageLocators) {
        const src = await img.getAttribute('src');
        expect(src).not.toBeNull();

        // Verify the image is not using the fallback placeholder
        expect(src).not.toContain(fallbackSrc!);

        // Verify the image source returns a successful HTTP status
        const response = await page.request.get(src!, { failOnStatusCode: false });
        expect(response.ok(), `Image src "${src}" failed to load with status ${response.status()}`).toBe(true);
    }
  });

  test('gallery_loads_with_valid_videos - Gallery displays videos with valid posters and inline playback works', async ({ page }) => {
    // Show only videos so the cards are on page 1 regardless of paging
    await page.goto('/?media-type=video');
    await expect(page.locator('.photo-card').first()).toBeVisible();

    // Playable videos carry an interactive ▶ badge on their card
    const videoCards = page.locator('.photo-card:has(.video-play-badge)');
    const videoCount = await videoCards.count();
    expect(videoCount).toBeGreaterThan(0);

    // Poster stills load and are not the fallback placeholder
    for (const img of await page.locator('.photo-card .photo-thumb img').all()) {
      const src = await img.getAttribute('src');
      const fallbackSrc = await img.getAttribute('data-fallback');
      expect(src).not.toBeNull();
      expect(src).not.toContain(fallbackSrc!);
      const response = await page.request.get(src!, { failOnStatusCode: false });
      expect(response.ok(), `Poster src "${src}" failed with status ${response.status()}`).toBe(true);
    }

    // Each video card shows its duration badge
    const firstCard = videoCards.first();
    await expect(firstCard.locator('.video-duration')).toBeVisible();

    // The video stream itself is servable
    const playBadge = firstCard.locator('button.video-play-badge');
    const mediaId = await playBadge.getAttribute('data-photo-id');
    expect(mediaId).not.toBeNull();
    const mediaResponse = await page.request.get(`/media/${mediaId}`, { failOnStatusCode: false });
    expect(mediaResponse.ok(), `Video src "/media/${mediaId}" failed with status ${mediaResponse.status()}`).toBe(true);

    // Clicking the badge swaps the poster for an inline, muted, autoplaying player
    await playBadge.click();
    const inlineVideo = firstCard.locator('video.video-inline');
    await expect(inlineVideo).toBeVisible();
    await expect(firstCard.locator('.photo-thumb')).toHaveClass(/is-playing/);
    await expect(firstCard.locator('.photo-thumb img')).toBeHidden();

    // It is actually playing (time advances), then pause it
    await expect.poll(() => inlineVideo.evaluate((v: HTMLVideoElement) => v.currentTime)).toBeGreaterThan(0);
    expect(await inlineVideo.evaluate((v: HTMLVideoElement) => v.paused)).toBe(false);
    await inlineVideo.evaluate((v: HTMLVideoElement) => v.pause());
    expect(await inlineVideo.evaluate((v: HTMLVideoElement) => v.paused)).toBe(true);

    // Inline play/pause must not have navigated off the gallery
    expect(new URL(page.url()).pathname).toBe('/');
  });

  test('gallery_filter_year_works - Should be able to find photos by filtering for year on the gallery page', async ({ page }) => {
    const initialPhotoCount = await getPhotoCount(page);
    expect(initialPhotoCount).toBeGreaterThan(0);

    // The native select is hidden behind the searchable-select widget
    const yearSelect = page.locator('select#year-select');
    await expect(yearSelect).toBeAttached();

    // Get a valid year from the dropdown (the second option, as the first is 'All Years')
    const yearToSelect = await yearSelect.locator('option').nth(1).getAttribute('value');
    expect(yearToSelect).not.toBeNull();
    expect(yearToSelect).not.toBe('');

    // Apply the year filter via the searchable-select widget
    await pickSearchableOption(page, 'select#year-select', yearToSelect!);
    await page.getByRole('button', { name: 'Apply Filters' }).click();
    await page.waitForURL(new RegExp(`[?&]year=${yearToSelect}`));
    
    await expect(page.locator('.photo-card').first()).toBeVisible();
    const filteredPhotoCount = await getPhotoCount(page);
    expect(filteredPhotoCount).toBeGreaterThan(0);
    expect(filteredPhotoCount).toBeLessThanOrEqual(initialPhotoCount);

    // Verify all displayed photos are from the selected year
    const photoDates = await page.locator('.photo-card .photo-date').allTextContents();
    for (const date of photoDates) {
        expect(date).toContain(yearToSelect!);
    }

    // Clear the filters
    await page.getByRole('button', { name: 'Clear Filters' }).click();
    await page.waitForURL('**/');

    // Verify the photo count returns to the initial state
    await expect(page.locator('.photo-card').nth(initialPhotoCount - 1)).toBeVisible();
    const clearedPhotoCount = await getPhotoCount(page);
    expect(clearedPhotoCount).toEqual(initialPhotoCount);
  });

  test('gallery_page_navigation_works - Verify that the page navigation works', async ({ page }) => {
    // Set page size to 10 via the searchable-select widget (the native select
    // is hidden; choosing an option navigates to the option's URL)
    const wrapper = page.locator('select#page-size + .searchable-select-wrapper');
    await wrapper.locator('.searchable-select-display').click();
    await wrapper.locator('.searchable-select-option').filter({ hasText: /^\s*10\s*$/ }).click();
    await page.waitForURL(/[?&]page=1&page-size=10/);

    await expect(page.locator('.photo-card')).toHaveCount(10);

    // Navigate to the next page
    const firstImageSrcPage1 = await page.locator('.photo-card img').first().getAttribute('src');
    const nextButton = page.getByRole('link', { name: 'Next ›' });
    await nextButton.click();
    await page.waitForURL(/[?&]page=2&page-size=10/);
    await expect(page.locator('.photo-card').first()).toBeVisible();
    const firstImageSrcPage2 = await page.locator('.photo-card img').first().getAttribute('src');
    expect(firstImageSrcPage1).not.toEqual(firstImageSrcPage2);

    // Navigate to the first page
    const firstButton = page.getByRole('link', { name: '« First' });
    await firstButton.click();
    await page.waitForURL(/[?&]page=1&page-size=10/);
    const firstImageSrcAfterReset = await page.locator('.photo-card img').first().getAttribute('src');
    expect(firstImageSrcAfterReset).toEqual(firstImageSrcPage1);

    // Navigate to the last page
    const lastButton = page.getByRole('link', { name: 'Last »' });
    await lastButton.click();
    await page.waitForLoadState('domcontentloaded');

    // On the last page, 'Next' and 'Last' should be disabled, and photo count should be <= page size
    const lastPageCount = await getPhotoCount(page);
    expect(lastPageCount).toBeGreaterThan(0);
    expect(lastPageCount).toBeLessThanOrEqual(10);
    await expect(nextButton).toHaveClass(/disabled/);
    await expect(lastButton).toHaveClass(/disabled/);
  });
});
