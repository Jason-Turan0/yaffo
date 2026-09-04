import { test, expect, Page } from '@playwright/test';
import {
  BASE_URL,
  CONTRACT_WIDTHS,
  VIEWPORTS,
  expectFitsViewport,
  expectNoPageOverflow,
  expectPanelContract,
  expectRouteFits,
  touchDrag,
  withTouchContext,
} from '../_support/responsive';

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
    // The pagination controls carry an aria-label ("Next"), which overrides
    // their visible "Next ›" text as the accessible name — and at 640px and
    // below the visible label is hidden entirely, leaving only the icon.
    const nextButton = page.getByRole('link', { name: 'Next' });
    await nextButton.click();
    await page.waitForURL(/[?&]page=2&page-size=10/);
    await expect(page.locator('.photo-card').first()).toBeVisible();
    const firstImageSrcPage2 = await page.locator('.photo-card img').first().getAttribute('src');
    expect(firstImageSrcPage1).not.toEqual(firstImageSrcPage2);

    // Navigate to the first page
    const firstButton = page.getByRole('link', { name: 'First' });
    await firstButton.click();
    await page.waitForURL(/[?&]page=1&page-size=10/);
    const firstImageSrcAfterReset = await page.locator('.photo-card img').first().getAttribute('src');
    expect(firstImageSrcAfterReset).toEqual(firstImageSrcPage1);

    // Navigate to the last page
    const lastButton = page.getByRole('link', { name: 'Last' });
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
    // page-size=10 forces the Bennett library into multiple batches
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

// ---------------------------------------------------------------------------
// Responsive coverage. Home is the reference implementation of the shared shell
// (docs/development/responsive.md), so the shell contract is verified here;
// every other page family verifies its own panels in its own spec. Shared
// assertions come from _support/responsive.ts — a page that re-implements an
// overflow or panel check has forked the contract.
// ---------------------------------------------------------------------------
test.describe('Photo Gallery Feature - Responsive', () => {

  /** The library view is a server-side preference; never leave it on timeline. */
  test.afterEach(async ({ page }) => {
    await page.request.get('/?view=grid');
  });

  test('gallery_fits_every_contract_viewport - the gallery and its shell never scroll the page sideways', async ({ page }) => {
    for (const width of CONTRACT_WIDTHS) {
      await page.setViewportSize({ width, height: 800 });
      await expectRouteFits(page, '/?view=grid');
      await expect(page.locator('.navbar')).toBeVisible();
      await expect(page.locator('.photo-grid')).toBeVisible();
    }
  });

  test('gallery_menu_is_reachable_on_a_narrow_screen - every destination survives the collapse', async ({ page }) => {
    await page.setViewportSize(VIEWPORTS.narrow);
    await page.goto('/?view=grid');

    // Closed, and taking up no layout space at all — not merely collapsed.
    const primary = page.locator('#navbar-primary');
    await expect(primary).toBeHidden();
    expect(await primary.boundingBox()).toBeNull();

    const menuToggle = page.locator('#nav-menu-toggle');
    await menuToggle.click();
    await expect(menuToggle).toHaveAttribute('aria-expanded', 'true');
    await expect(primary).toBeVisible();
    await expect(page.locator('.navbar-nav .nav-link')).toHaveCount(9);
    for (const name of ['Home', 'Albums', 'Faces', 'People', 'Locations', 'Utilities', 'Sharing', 'Themes', 'Settings']) {
      await expect(page.locator('.navbar-nav').getByRole('link', { name, exact: true })).toBeVisible();
    }

    await page.keyboard.press('Escape');
    await expect(menuToggle).toHaveAttribute('aria-expanded', 'false');
    await expect(primary).toBeHidden();
  });

  test('gallery_filters_use_a_peer_navbar_panel - Filters is a peer of Menu, not a disclosure inside it', async ({ page }) => {
    // Closed on first paint, peer of Menu, mutually exclusive, live DOM restored
    // to the page (with its values) on the way back to desktop.
    await expectPanelContract(page, { route: '/?view=grid', panelId: 'home-filters' });

    await page.setViewportSize(VIEWPORTS.narrow);
    const filtersToggle = page.locator('#home-filters-toggle');
    const menuToggle = page.locator('#nav-menu-toggle');
    await expect(filtersToggle).toBeVisible();
    const filtersBox = (await filtersToggle.boundingBox())!;
    const menuBox = (await menuToggle.boundingBox())!;
    expect(filtersBox.height).toBeGreaterThanOrEqual(44);
    expect(menuBox.height).toBeGreaterThanOrEqual(44);
    // Menu always sorts last, and the two targets keep an 8px gap.
    expect(menuBox.x - (filtersBox.x + filtersBox.width)).toBeGreaterThanOrEqual(8);

    await filtersToggle.click();
    const panel = page.locator('#home-filters');
    await expect(panel).toBeVisible();
    // In the navbar the panel drops the desktop card: framing a panel that is
    // already inside one reads as an empty box and nests a second scroller.
    expect(await panel.evaluate((element) => getComputedStyle(element).boxShadow)).toBe('none');
    expect(await panel.evaluate((element) => getComputedStyle(element).paddingTop)).toBe('0px');
    await expectFitsViewport(page, '#navbar-context-panels');
    await expectNoPageOverflow(page);

    await page.keyboard.press('Escape');
    await expect(filtersToggle).toHaveAttribute('aria-expanded', 'false');
    await expect(panel).toBeHidden();
  });

  test('gallery_narrow_ui_is_closed_before_javascript_runs - navigation never flashes the menu open', async ({ browser }) => {
    // No JavaScript at all: whatever is visible here is what the user sees in
    // the gap between HTML arriving and nav.js running on every navigation.
    const context = await browser.newContext({
      baseURL: BASE_URL,
      viewport: VIEWPORTS.narrow,
      javaScriptEnabled: false,
    });
    try {
      const page = await context.newPage();
      await page.goto('/?view=grid');
      await expect(page.locator('#nav-menu-toggle')).toBeVisible();
      await expect(page.locator('#home-filters-toggle')).toBeVisible();
      await expect(page.locator('#navbar-primary')).toBeHidden();
      await expect(page.locator('#navbar-pages-bar')).toBeHidden();
      await expect(page.locator('#home-filters')).toBeHidden();
      await expectNoPageOverflow(page);
    } finally {
      await context.close();
    }
  });

  test('gallery_filters_button_counts_applied_filters - the closed button says how many filters are narrowing the view', async ({ page }) => {
    await page.setViewportSize(VIEWPORTS.narrow);

    await page.goto('/?view=grid');
    const badge = page.locator('#home-filters-toggle [data-nav-panel-count]');
    await expect(badge).toBeHidden();

    // A multi-valued filter counts once; a pagination key does not count at all.
    await page.goto('/?view=grid&favorite=true&person=1&person=2&page=1');
    await expect(badge).toBeVisible();
    await expect(badge).toHaveText('2');
  });

  test('gallery_pagination_is_one_icon_row_on_mobile - four controls stay on one reachable row', async ({ page }) => {
    await page.setViewportSize(VIEWPORTS.narrow);
    await page.goto('/?view=grid&page-size=10');

    const buttons = page.locator('.page-navigation .page-btn');
    await expect(buttons).toHaveCount(4);
    const boxes = await buttons.evaluateAll((elements) =>
      elements.map((element) => element.getBoundingClientRect().toJSON()));
    for (const box of boxes) {
      expect(box.width).toBeGreaterThanOrEqual(44);
      expect(box.height).toBeGreaterThanOrEqual(44);
      expect(box.y).toBeCloseTo(boxes[0].y, 0);
    }
    await expect(page.locator('.page-btn-label').first()).toBeHidden();
    await expectNoPageOverflow(page);

    // Paging must not leave the menu open behind the new document.
    await page.locator('.page-btn[data-icon="page-next"]').click();
    await page.waitForURL(/[?&]page=2/);
    await expect(page.locator('#nav-menu-toggle')).toHaveAttribute('aria-expanded', 'false');
    await expect(page.locator('#navbar-primary')).toBeHidden();

    // Desktop keeps its localized text labels.
    await page.setViewportSize(VIEWPORTS.desktop);
    await expect(page.locator('.page-btn-label').first()).toBeVisible();
  });

  test('gallery_filter_configurator_fits_a_narrow_viewport - the configure dialog is usable at 320px', async ({ page }) => {
    await page.setViewportSize(VIEWPORTS.minimum);
    await page.goto('/?view=grid');

    await page.locator('#home-filters-toggle').click();
    await page.locator('#configure-filters-btn').click();
    const modal = page.locator('#configureFiltersModal');
    await expect(modal).toHaveClass(/active/);
    await expectFitsViewport(page, '#configureFiltersModal .modal-content');
    await expectNoPageOverflow(page);

    // The modal body is the only scroll region and contains its own overscroll.
    const body = modal.locator('.modal-body');
    const bodyStyles = await body.evaluate((element) => {
      const style = getComputedStyle(element);
      return { overflowY: style.overflowY, overscroll: style.overscrollBehavior };
    });
    expect(['auto', 'scroll']).toContain(bodyStyles.overflowY);
    expect(bodyStyles.overscroll).toContain('contain');
    await expect(modal.locator('.modal-actions')).toHaveCSS('flex-wrap', 'wrap');

    // Escape belongs to the topmost surface: the dialog closes, the panel that
    // opened it stays open behind it.
    await page.keyboard.press('Escape');
    await expect(modal).not.toHaveClass(/active/);
    await expect(page.locator('#home-filters-toggle')).toHaveAttribute('aria-expanded', 'true');
  });

  test('gallery_filter_order_can_be_changed_by_touch - reordering works with a real touch drag', async ({ browser }) => {
    await withTouchContext(browser, VIEWPORTS.narrow, async (page, context) => {
      await page.goto('/?view=grid');
      await page.locator('#home-filters-toggle').click();
      await page.locator('#configure-filters-btn').click();
      await expect(page.locator('#configureFiltersModal')).toHaveClass(/active/);

      const rows = page.locator('.filter-config-row');
      expect(await rows.count()).toBeGreaterThan(1);
      const before = await rows.evaluateAll((elements) => elements.map((element) => element.getAttribute('data-key')));

      const handle = rows.first().locator('.filter-config-handle');
      const handleBox = (await handle.boundingBox())!;
      expect(handleBox.width).toBeGreaterThanOrEqual(44);
      expect(handleBox.height).toBeGreaterThanOrEqual(44);

      // Chrome's real emulated touch stream: synthetic pointer events pass
      // against handlers a finger never reaches.
      const target = (await rows.nth(1).boundingBox())!;
      await touchDrag(
        context,
        page,
        { x: handleBox.x + handleBox.width / 2, y: handleBox.y + handleBox.height / 2 },
        { x: handleBox.x + handleBox.width / 2, y: target.y + target.height },
      );

      const after = await rows.evaluateAll((elements) => elements.map((element) => element.getAttribute('data-key')));
      expect(after[0]).toBe(before[1]);
      expect(after[1]).toBe(before[0]);
      await expect(page.locator('.filter-config-row.dragging')).toHaveCount(0);
    });
  });

  test('gallery_survives_rtl_zoom_and_short_landscape - the shell stays contained in the awkward cases', async ({ page }) => {
    await page.setViewportSize(VIEWPORTS.narrow);
    await page.goto('/?view=grid');
    await page.evaluate(() => document.documentElement.setAttribute('dir', 'rtl'));
    await expectNoPageOverflow(page);
    await page.locator('#nav-menu-toggle').click();
    await expect(page.locator('#navbar-primary')).toBeVisible();
    await expectNoPageOverflow(page);

    // 200% text zoom on a short landscape viewport.
    await page.setViewportSize(VIEWPORTS.narrowLandscape);
    await page.goto('/?view=grid');
    await page.evaluate(() => { document.documentElement.style.fontSize = '32px'; });
    await expectNoPageOverflow(page);
    await page.locator('#nav-menu-toggle').click();
    await expect(page.locator('.navbar-nav').getByRole('link', { name: 'Home', exact: true })).toBeVisible();
    await expectNoPageOverflow(page);
  });

  test('gallery_shell_holds_up_in_every_built_in_theme - skins decorate the narrow shell without moving it', async ({ page }) => {
    await page.setViewportSize(VIEWPORTS.narrow);
    await page.goto('/?view=grid');

    // The theme stylesheet is swapped in place rather than made the app-wide
    // default: the geometry under test comes from the sheet, and a global
    // setting change would leak into whatever else is running.
    for (const slug of ['classic', 'darkroom', 'memphis', 'neobrutalist', 'photos-app', 'scrapbook']) {
      await page.evaluate(async (theme) => {
        const link = document.querySelector<HTMLLinkElement>('link[href*="/theme.css"]');
        if (!link) throw new Error('No theme stylesheet is linked');
        await new Promise<void>((resolve, reject) => {
          link.addEventListener('load', () => resolve(), { once: true });
          link.addEventListener('error', () => reject(new Error(`theme ${theme} failed to load`)), { once: true });
          // A cache-busting param: assigning the href the page already has is a
          // no-op, and the load event would never arrive.
          link.href = `/themes/${theme}/theme.css?probe=${Date.now()}`;
        });
        document.documentElement.setAttribute('data-theme', theme);
      }, slug);
      await expect(page.locator('html')).toHaveAttribute('data-theme', slug);
      await expectNoPageOverflow(page);
    }
  });

  test('every_page_registers_its_panels_the_same_way - one narrow navigation system, not two', async ({ page, request }) => {
    const routes = ['/', '/albums', '/faces', '/people', '/locations', '/utilities', '/sharing', '/themes', '/settings'];
    for (const route of routes) {
      const response = await request.get(route);
      expect(response.ok(), `${route} did not render`).toBeTruthy();
      const html = await response.text();
      const panels = (html.match(/data-nav-panel(?![-a-zA-Z])/g) || []).length;
      const toggles = (html.match(/data-nav-panel-toggle/g) || []).length;
      expect(panels, `${route} registers ${panels} panels for ${toggles} peer buttons`).toBe(toggles);
      // The legacy generic initializer is retired; nothing may generate one.
      expect(html, `${route} still renders a legacy disclosure`).not.toContain('responsive-panel-toggle');
    }

    // And in the live DOM, every panel is addressed by exactly one peer button.
    await page.setViewportSize(VIEWPORTS.narrow);
    await page.goto('/?view=grid');
    const wiring = await page.evaluate(() => Array.from(document.querySelectorAll('[data-nav-panel]')).map((panel) => ({
      id: panel.id,
      buttons: document.querySelectorAll(`[data-nav-panel-toggle][aria-controls="${panel.id}"]`).length,
    })));
    expect(wiring.length).toBeGreaterThan(0);
    for (const panel of wiring) {
      expect(panel.id).not.toBe('');
      expect(panel.buttons, `panel ${panel.id}`).toBe(1);
    }
    expect(await page.locator('.responsive-panel-toggle').count()).toBe(0);
  });

  test('gallery_choosing_a_filter_value_keeps_the_panel_open - picking a value does not dismiss the panel', async ({ page }) => {
    await page.setViewportSize(VIEWPORTS.narrow);
    await page.goto('/?view=grid');
    await page.locator('#home-filters-toggle').click();
    await expect(page.locator('#home-filters')).toBeVisible();

    const yearSelect = page.locator('select#year-select');
    const year = await yearSelect.locator('option').nth(1).getAttribute('value');
    await pickSearchableOption(page, 'select#year-select', year!);

    // REGRESSION - the searchable select rebuilds its option list on change,
    // which detaches the clicked node while the click is still bubbling; the
    // navbar's outside-click dismissal has to judge the click by its event
    // path, not by whether the target is still in the document.
    await expect(page.locator('#home-filters-toggle')).toHaveAttribute('aria-expanded', 'true');
    await expect(page.locator('#home-filters')).toBeVisible();
    await expect(yearSelect).toHaveValue(year!);
  });

  test('gallery_filter_controls_sit_inline_with_their_labels - checkbox and radio rows line up with their text', async ({ page }) => {
    await page.setViewportSize(VIEWPORTS.narrow);
    await page.goto('/?view=grid');
    await page.locator('#home-filters-toggle').click();

    const wrapper = page.locator('.multi-select-wrapper').first();
    await wrapper.locator('.multi-select-header').click();
    const options = wrapper.locator('.multi-select-option');
    expect(await options.count()).toBeGreaterThan(1);

    // REGRESSION - the sidebar's field-label rule must not match labels that a
    // component renders inside a filter group, or it outranks their own layout
    // and they collapse to block flow.
    await expect(options.first()).toHaveCSS('display', 'flex');
    await expect(options.first()).toHaveCSS('align-items', 'center');
    await expect(options.first().locator('input[type="checkbox"]')).toHaveCSS('margin-right', '8px');

    // Two values reveal the any/all match-type row, which is laid out the same way.
    await options.nth(0).click();
    await options.nth(1).click();
    const matchOption = page.locator('.match-type:visible .match-option').first();
    await expect(matchOption).toBeVisible();
    await expect(matchOption).toHaveCSS('display', 'flex');
    await expect(matchOption).toHaveCSS('align-items', 'center');
    await expect(matchOption).toHaveCSS('gap', '5px');
    await expectNoPageOverflow(page);
  });

  test('gallery_timeline_offers_a_touch_safe_jump_control - a thumb can reach a date without the drag rail', async ({ browser }) => {
    await withTouchContext(browser, VIEWPORTS.narrow, async (page) => {
      await page.goto('/?view=timeline&page-size=10');

      // REGRESSION - the scrubber was simply hidden below 900px, which left a
      // touch screen with NO way to jump to a date: the drag on a 44px rail
      // pinned to the window edge was the only path, and it was gone.
      await expect(page.locator('#timeline-scrubber')).toBeHidden();
      const jump = page.locator('.timeline-jump');
      await expect(jump).toBeVisible();
      const years = jump.locator('.timeline-jump-year');
      expect(await years.count()).toBeGreaterThan(0);

      const yearBox = (await years.first().boundingBox())!;
      expect(yearBox.height).toBeGreaterThanOrEqual(44);
      expect(yearBox.width).toBeGreaterThanOrEqual(44);

      // Its own scroller: a long-running library must not widen the document.
      await expect(jump).toHaveCSS('overflow-x', 'auto');
      await expectNoPageOverflow(page);

      // It stays reachable while the timeline is scrolled.
      await page.evaluate(() => window.scrollTo(0, 600));
      await expect(jump).toBeInViewport();
      await page.request.get('/?view=grid');
    });
  });

  test('gallery_timeline_jump_control_lands_below_the_sticky_navbar - tapping a year reaches that month', async ({ browser }) => {
    await withTouchContext(browser, VIEWPORTS.narrow, async (page) => {
      await page.goto('/?view=timeline&page-size=10');
      const years = page.locator('.timeline-jump .timeline-jump-year');
      await expect(years.last()).toBeVisible();

      await years.last().tap();
      await page.waitForURL(/#month-\d{4}-\d{2}/);
      const hash = new URL(page.url()).hash;
      const divider = page.locator(hash);
      await expect(divider).toBeVisible();

      // The landing clears the sticky navbar AND the jump bar stuck below it —
      // the anchor's scroll-margin has to account for both.
      const jumpBox = (await page.locator('.timeline-jump').boundingBox())!;
      expect((await divider.boundingBox())!.y).toBeGreaterThanOrEqual(jumpBox.y + jumpBox.height - 1);
      await page.request.get('/?view=grid');
    });
  });

  test('gallery_timeline_fits_every_contract_viewport - streamed day sections stay contained', async ({ page }) => {
    for (const width of CONTRACT_WIDTHS) {
      await page.setViewportSize({ width, height: 800 });
      await expectRouteFits(page, '/?view=timeline&page-size=10');
      await expect(page.locator('.timeline-section').first()).toBeVisible();
    }

    // Scrolled, the sticky day header has to clear the navbar AND the jump bar
    // stuck below it, instead of sliding underneath.
    await page.setViewportSize(VIEWPORTS.narrow);
    await page.goto('/?view=timeline&page-size=10');
    await page.evaluate(() => window.scrollTo(0, 600));
    const jumpBox = (await page.locator('.timeline-jump').boundingBox())!;
    const headerTop = await page.locator('.timeline-day-header').first()
      .evaluate((element) => parseFloat(getComputedStyle(element).top));
    expect(headerTop).toBeGreaterThanOrEqual(jumpBox.y + jumpBox.height - 1);
  });

  test('gallery_timeline_streaming_survives_rotation - a streamed batch is not lost by a resize', async ({ page }) => {
    await page.setViewportSize(VIEWPORTS.narrow);
    await page.goto('/?view=timeline&page-size=10');
    const firstBatch = await getPhotoCount(page);
    expect(firstBatch).toBe(10);

    const sentinel = page.locator('.timeline-sentinel');
    await sentinel.scrollIntoViewIfNeeded();
    await expect.poll(() => getPhotoCount(page)).toBeGreaterThan(firstBatch);
    const streamed = await getPhotoCount(page);

    // Rotation is a layout change, not a navigation: nothing already streamed
    // in may be dropped, and the wider layout must still contain itself.
    await page.setViewportSize(VIEWPORTS.narrowLandscape);
    expect(await getPhotoCount(page)).toBe(streamed);
    await expectNoPageOverflow(page);
    await expect(page.locator('.timeline-section.is-continuation')).toHaveCount(0);
  });

  test('gallery_card_details_have_a_coarse_pointer_path - the hover overlay never traps a touch screen', async ({ browser }) => {
    await withTouchContext(browser, VIEWPORTS.narrow, async (page) => {
      await page.goto('/?view=grid');
      const card = page.locator('.photo-card').first();

      // The overlay is hover-only: on touch it either never opens or opens on
      // tap and sticks, covering the thumbnail it describes. The card's own tap
      // target leads to the detail page, which shows all of it.
      await expect(card.locator('.photo-hover')).toBeHidden();

      // REGRESSION - the favourite heart was faded in by card hover, so on a
      // touch screen the only way to favourite from the grid was invisible.
      const favorite = card.locator('.favorite-toggle');
      await expect(favorite).toBeVisible();
      const box = (await favorite.boundingBox())!;
      expect(box.width).toBeGreaterThanOrEqual(44);
      expect(box.height).toBeGreaterThanOrEqual(44);

      const wasFavorite = await favorite.evaluate((element) => element.classList.contains('is-favorite'));
      await favorite.tap();
      await expect(favorite).toHaveClass(wasFavorite ? /^((?!is-favorite).)*$/ : /is-favorite/);
      await favorite.tap();
      await expect(favorite).toHaveClass(wasFavorite ? /is-favorite/ : /^((?!is-favorite).)*$/);
    });
  });
});
