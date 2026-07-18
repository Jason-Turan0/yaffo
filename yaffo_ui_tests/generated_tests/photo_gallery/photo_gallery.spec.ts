import { test, expect, Page } from '@playwright/test';

// The library view (grid vs timeline) is a SERVER-SIDE preference: any request
// with ?view=<x> persists <x> in ApplicationSettings, and a request without the
// param renders whatever was last saved. Running this file's tests in parallel
// would let them race on that shared setting, so run them sequentially in one
// worker. Every test also navigates with an explicit ?view= param so its own
// render never depends on what a previous test saved (only
// timeline_view_preference_persists reads the saved value, on purpose).
test.describe.configure({ mode: 'default' });

// Helper function to get the number of visible photos
async function getPhotoCount(page: Page): Promise<number> {
  return page.locator('.photo-card').count();
}

// The page-header subtitle's photo total: "Showing 10 of 16 photos" (grid) or
// "16 photos" (timeline) — the LAST number is the filtered library total.
async function getHeaderPhotoTotal(page: Page): Promise<number> {
  const text = await page.locator('.page-header .subtitle').innerText();
  const match = text.match(/([\d,]+)\s+photos?\s*$/);
  expect(match, `Expected a photo total in header text "${text}"`).not.toBeNull();
  return Number(match![1].replace(/,/g, ''));
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
    // Explicit ?view=grid: deterministic regardless of the saved view preference
    await page.goto('/?view=grid');
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
    await page.goto('/?media-type=video&view=grid');
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

  test('timeline_view_shows_day_sections - Timeline view groups photos into day sections with month dividers', async ({ page }) => {
    await page.goto('/?view=timeline');
    await expect(page.locator('.timeline')).toBeVisible();

    // The view toggle marks Timeline active
    const viewToggle = page.locator('.view-toggle');
    await expect(viewToggle.getByRole('link', { name: 'Timeline' })).toHaveClass(/is-active/);
    await expect(viewToggle.getByRole('link', { name: 'Grid' })).not.toHaveClass(/is-active/);

    // Timeline header reports the library total, not "Showing N of M"
    await expect(page.locator('.page-header .subtitle')).toHaveText(/^\s*[\d,]+ photos?\s*$/);

    // Day sections with a header (label + item count)
    const sections = page.locator('.timeline-section');
    expect(await sections.count()).toBeGreaterThan(0);
    const firstHeader = sections.first().locator('.timeline-day-header');
    await expect(firstHeader.locator('.timeline-day-label')).toBeVisible();
    await expect(firstHeader.locator('.timeline-day-count')).toHaveText(/[\d,]+ items?/);

    // Month dividers with the scrubber's #month-YYYY-MM anchor ids
    const dividers = page.locator('.timeline-month-divider');
    expect(await dividers.count()).toBeGreaterThan(0);
    expect(await dividers.first().getAttribute('id')).toMatch(/^month-\d{4}-\d{2}$/);

    // Every dated section only holds photos taken that day (card dates carry the year)
    for (const section of await sections.all()) {
      const sectionDate = await section.getAttribute('data-date');
      if (!sectionDate || sectionDate === 'unknown') continue;
      for (const cardDate of await section.locator('.photo-date').allTextContents()) {
        expect(cardDate).toContain(sectionDate.slice(0, 4));
      }
    }

    // Timeline streams instead of paginating: no pagination buttons
    await expect(page.locator('.page-btn')).toHaveCount(0);

    // All rendered thumbnails resolve (no fallback placeholder, HTTP 200)
    for (const img of await page.locator('.timeline .photo-card img').all()) {
      const src = await img.getAttribute('src');
      const fallbackSrc = await img.getAttribute('data-fallback');
      expect(src).not.toBeNull();
      expect(src).not.toContain(fallbackSrc!);
      const response = await page.request.get(src!, { failOnStatusCode: false });
      expect(response.ok(), `Thumb src "${src}" failed with status ${response.status()}`).toBe(true);
    }
  });

  test('timeline_infinite_scroll_streams_more_photos - Revealing the sentinel streams the next batch until the library is exhausted', async ({ page }) => {
    // page-size=10 forces the 16-item library into multiple batches
    await page.goto('/?view=timeline&page-size=10');
    await expect(page.locator('.timeline')).toBeVisible();

    const total = await getHeaderPhotoTotal(page);
    const firstBatch = await getPhotoCount(page);
    expect(firstBatch).toBe(10);
    expect(total).toBeGreaterThan(firstBatch);

    // More remains, so the htmx sentinel is present
    const sentinel = page.locator('.timeline-sentinel');
    await expect(sentinel).toHaveCount(1);

    // Reveal the sentinel until every batch has streamed in (each swap may
    // append a fresh sentinel; the last batch omits it)
    for (let guard = 0; guard < 10 && (await sentinel.count()) > 0; guard++) {
      const before = await getPhotoCount(page);
      await sentinel.scrollIntoViewIfNeeded();
      await expect.poll(() => getPhotoCount(page)).toBeGreaterThan(before);
    }
    await expect(sentinel).toHaveCount(0);
    expect(await getPhotoCount(page)).toBe(total);

    // Continuation batches merged into the previous day: no leftover
    // .is-continuation sections and no day rendered twice
    await expect(page.locator('.timeline-section.is-continuation')).toHaveCount(0);
    const sectionDates = await page.locator('.timeline-section').evaluateAll(
      (els) => els.map((el) => el.getAttribute('data-date') || ''));
    expect(new Set(sectionDates).size).toBe(sectionDates.length);
  });

  test('timeline_scrubber_jumps_to_date - The date scrubber shows density bars and jumps to a month', async ({ page }) => {
    await page.goto('/?view=timeline&page-size=10');
    const scrubber = page.locator('#timeline-scrubber');
    await expect(scrubber).toBeVisible();

    // Density bars and year marks render on the rail
    expect(await scrubber.locator('.timeline-scrubber-bar').count()).toBeGreaterThan(0);
    const yearMarks = scrubber.locator('.timeline-scrubber-year');
    expect(await yearMarks.count()).toBeGreaterThan(0);

    // Year marks are real links (the no-JS fallback) into the timeline
    const oldestMarkHref = await yearMarks.last().getAttribute('href');
    expect(oldestMarkHref).toMatch(/view=timeline/);
    expect(oldestMarkHref).toMatch(/#month-\d{4}-\d{2}$/);

    // With JS active the whole rail is one pointer target: pressing near the
    // bottom jumps to (about) the oldest month — location.assign to the month's
    // page with a #month-YYYY-MM anchor
    const railBox = (await scrubber.boundingBox())!;
    await page.mouse.click(railBox.x + railBox.width / 2, railBox.y + railBox.height - 2);
    await page.waitForURL(/#month-\d{4}-\d{2}/);

    // The landing renders that month: its divider anchor exists and its day
    // sections carry the month's date prefix
    const hash = new URL(page.url()).hash;
    await expect(page.locator(hash)).toBeVisible();
    const monthPrefix = hash.replace('#month-', '');
    expect(await page.locator(`.timeline-section[data-date^="${monthPrefix}"]`).count()).toBeGreaterThan(0);
    await expect(page.locator('.timeline')).toBeVisible();
  });

  test('timeline_view_preference_persists - The chosen library view is saved and applied to later visits', async ({ page }) => {
    try {
      // Visiting with ?view=timeline persists the preference server-side
      await page.goto('/?view=timeline');
      await expect(page.locator('.timeline')).toBeVisible();

      // A visit WITHOUT the param renders the saved preference
      await page.goto('/');
      await expect(page.locator('.timeline')).toBeVisible();
      await expect(page.locator('.view-toggle').getByRole('link', { name: 'Timeline' })).toHaveClass(/is-active/);

      // Switching back via the toggle persists grid again
      await page.locator('.view-toggle').getByRole('link', { name: 'Grid' }).click();
      await page.waitForURL(/[?&]view=grid/);
      await expect(page.locator('.timeline')).toHaveCount(0);
      await page.goto('/');
      await expect(page.locator('.timeline')).toHaveCount(0);
      await expect(page.locator('.photo-grid')).toBeVisible();
      await expect(page.locator('.view-toggle').getByRole('link', { name: 'Grid' })).toHaveClass(/is-active/);
    } finally {
      // Never leave the shared server-side preference on timeline: other
      // suites navigate to '/' bare and expect the grid default
      await page.request.get('/?view=grid');
    }
  });

  test('timeline_respects_filters - Filters constrain the timeline and keep the timeline view active', async ({ page }) => {
    // This also saves timeline as the preference, which Clear Filters (a bare
    // '/' navigation) falls back to — the serial file makes that deterministic
    await page.goto('/?view=timeline');
    await expect(page.locator('.timeline')).toBeVisible();
    const total = await getHeaderPhotoTotal(page);

    // Pick the newest concrete year from the (hidden) native select
    const yearSelect = page.locator('select#year-select');
    await expect(yearSelect).toBeAttached();
    const yearToSelect = await yearSelect.locator('option').nth(1).getAttribute('value');
    expect(yearToSelect).not.toBeNull();
    expect(yearToSelect).not.toBe('');

    await pickSearchableOption(page, 'select#year-select', yearToSelect!);
    await page.getByRole('button', { name: 'Apply Filters' }).click();
    await page.waitForURL(new RegExp(`[?&]year=${yearToSelect}`));

    // The filter form carries the view along as a hidden input
    expect(page.url()).toMatch(/[?&]view=timeline/);
    await expect(page.locator('.timeline')).toBeVisible();

    // Header total shrank to the filtered set and matches what rendered
    // (a filtered year fits in one default-size batch)
    const filteredTotal = await getHeaderPhotoTotal(page);
    expect(filteredTotal).toBeGreaterThan(0);
    expect(filteredTotal).toBeLessThan(total);
    expect(await getPhotoCount(page)).toBe(filteredTotal);

    // Every day section is from the selected year
    const sectionDates = await page.locator('.timeline-section').evaluateAll(
      (els) => els.map((el) => el.getAttribute('data-date') || ''));
    expect(sectionDates.length).toBeGreaterThan(0);
    for (const date of sectionDates) {
      expect(date).toMatch(new RegExp(`^${yearToSelect}-`));
    }

    // Clear Filters navigates to bare '/' → saved (timeline) preference applies
    await page.getByRole('button', { name: 'Clear Filters' }).click();
    await page.waitForURL('**/');
    await expect(page.locator('.timeline')).toBeVisible();
    expect(await getHeaderPhotoTotal(page)).toBe(total);

    // Leave the shared preference on grid for whoever runs next
    await page.request.get('/?view=grid');
  });
});