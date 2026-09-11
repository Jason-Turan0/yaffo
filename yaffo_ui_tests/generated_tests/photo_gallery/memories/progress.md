# Photo Gallery Tests — Current State (2026-07-18)

## Status: PASSING (9/9) against the isolated sandbox

## Application facts (verified against live app)
- Gallery is the home page `/`. Container: `.photo-grid`; items: `.photo-card`; card date: `.photo-date`; hover overlay: `.photo-hover`.
- Photo cards open details at `/media/view/{id}` (photos→media rename). Images served from `/media/{id}` with `data-fallback` = `/placeholder`.
- Seeded test data: 14 photos + 2 h264 videos (16 media items), multiple years, so page-size 10 yields 2 pages.
- Pagination component: `.page-btn` anchors named `« First`, `‹ Previous`, `Next ›`, `Last »`; disabled state = `.disabled` class + `onclick="return false"`.

## Library view toggle + DETERMINISM RULE (do not undo)
- `/` has a Grid/Timeline toggle: `.view-toggle` links named `Grid` / `Timeline`; the active one carries `.is-active` and `aria-current="true"`.
- The view is a **server-side preference** (`ApplicationSettings` row `library_view`, saved by `_resolve_library_view` in `yaffo/routes/home.py`): ANY request with `?view=<x>` persists `<x>`; a bare `/` renders the saved value.
- Because of that, the spec file sets `test.describe.configure({ mode: 'default' })` (whole file sequential in one worker — overrides `fullyParallel`) and **every navigation passes an explicit `?view=` param** (`beforeEach` → `/?view=grid`). Only `timeline_view_preference_persists` reads the saved value on purpose, and it restores `/?view=grid` in a `finally`. Removing the serial config or the explicit params reintroduces cross-test races on the shared preference.
- `Clear Filters` is `window.location.href = '/'` (no params) → lands on the SAVED view. The timeline filter test relies on this: it enters via `?view=timeline` (which saves timeline), so after Clear the page is still the timeline.

## Timeline view DOM
- Container `.timeline`; day sections `<section class="timeline-section" data-date="YYYY-MM-DD"|"unknown">`, each with `.timeline-day-header` (`.timeline-day-label` + `.timeline-day-count` "N items") and an inner `.photo-grid` of `.photo-card`s. Month changes render `<h2 class="timeline-month-divider" id="month-YYYY-MM">`.
- Header subtitle `.page-header .subtitle` shows the TOTAL only ("16 photos"); grid shows "Showing N of 16 photos". Helper `getHeaderPhotoTotal` parses the last number.
- No pagination in timeline (`.page-btn` count 0). Infinite scroll: `<div class="timeline-sentinel" hx-trigger="revealed" hx-swap="outerHTML">` fetches `/?…&page=N+1&fragment=sections`. To stream: `scrollIntoViewIfNeeded()` the sentinel, then `expect.poll` the `.photo-card` count. Use `page-size=10` to force multiple batches.
- A batch continuing the previous day arrives as `.timeline-section.is-continuation`; `timeline_stream.js` merges it into the section above on `htmx:afterSwap`. After full streaming: zero `.is-continuation` sections and all section `data-date`s unique — that's the merge assertion.

## Timeline scrubber gotcha
- `nav#timeline-scrubber` holds `.timeline-scrubber-bar` density bars and `.timeline-scrubber-year` links (href = page URL + `#month-YYYY-MM`, the no-JS fallback).
- `timeline_scrubber.js` TAKES OVER the rail's pointer events: pointerdown is preventDefault'd, pointerup does `location.assign(jumpUrl(monthAtPointerY))`, and the year link's own click is suppressed. So do NOT assert that clicking a year link navigates to its href. Also crowded year labels get `visibility: hidden` (Playwright click refuses them).
- Working pattern: `page.mouse.click()` on the rail's bounding box near the bottom (oldest end) → `waitForURL(/#month-\d{4}-\d{2}/)` → assert the `#month-YYYY-MM` divider is visible and a `.timeline-section[data-date^="YYYY-MM"]` exists. Don't assert the exact month/year — it depends on click Y.

## Videos in the gallery
- Filter to videos with `/?media-type=video` (`#media-type-select` in the sidebar).
- A video card shows a poster `<img src="/media/{id}/poster" data-fallback="/static/video_placeholder.svg">`, a `.video-duration` badge, and — only when the format is browser-playable — a `<button class="video-play-badge" data-photo-id="{id}">`.
- Clicking the badge swaps in an inline `<video class="video-inline">` (muted + autoplay + controls), hides the poster/duration/badge, adds `.is-playing` to `.photo-thumb`, and must NOT navigate to the detail view (player clicks stopPropagation). The stream is `/media/{id}`.
- On playback error the card restores its poster/badge and shows an error toast (`media:gallery.videoPlaybackFailed`).
- Playwright's bundled Chromium DOES play h264 here (canPlayType → "probably") — verified empirically; assert playback via `video.currentTime > 0` rather than trusting autoplay timing.

## Critical gotcha: searchable-select widgets
Every `<select class="searchable-select">` (`#year-select`, `#page-size`, etc.) is hidden (`display:none`) behind a custom widget inserted immediately after it. `locator.selectOption()` TIMES OUT on these. Interact via:

```ts
const wrapper = page.locator('select#year-select + .searchable-select-wrapper');
await wrapper.locator('.searchable-select-display').click();
await wrapper.locator('.searchable-select-option').filter({ hasText: value }).first().click();
```

- `#page-size` option VALUES are full URLs (`/?page=1&page-size=10&...`); choosing one navigates via the select's onchange. Option text has surrounding whitespace — match with `/^\s*10\s*$/` to avoid matching "100".
- The filter form GET-submits every filter field, so don't assert exact query strings; use regexes like `page.waitForURL(/[?&]year=2014/)`. In timeline view the form also carries `<input type="hidden" name="view" value="timeline">`, so Apply Filters keeps `view=timeline` in the URL.

## History of resolved issues (do not re-investigate)
- `.gallery-grid` selector bug → fixed long ago (`.photo-grid` is correct).
- 2026-07: `selectOption` timeouts on year/page-size selects → caused by the searchable-select migration, fixed as above.
- 2026-07-18: added the 5 timeline tests + the determinism rework (serial file, explicit `?view=` everywhere). All 9 pass in 7.4s.

## Responsive coverage (2026-08-30) — 27/27 passing

### How to run
`npm run test:spec -- generated_tests/photo_gallery/photo_gallery.spec.ts --port <yours>`.
The file is serial (`mode: 'default'`), so the responsive block runs after the
behaviour block; every responsive test restores `/?view=grid` in an `afterEach`.

### Shell facts (Home is where the shared contract is verified)
- `nav.js`'s narrow breakpoint is **`max-width: 1200px`**, not 640/900 — so the
  Menu and panel toggles are present at 1024 as well. Structural layout in
  `responsive.css` keys off the same width.
- Home registers exactly one panel: `#home-filters` (from `_sidebar.html`'s
  `panel_prefix="home"`), peer button `#home-filters-toggle`, host
  `#navbar-context-panels`. `sidebar_toggles('home')` renders Actions only when
  asked, and Home does not ask.
- `expectPanelContract` leaves the page at **1440px**. Resizing back to narrow
  works, but nav.js re-parks on the matchMedia `change` event, so `await
  expect(toggle).toBeVisible()` before measuring a box or `boundingBox()` is null.
- Applied-filter badge: `#home-filters-toggle [data-nav-panel-count]`, server
  rendered by `applied_filter_count`. `page`, `page-size`, `view`, `sort`,
  `edit`, `scope`, `label`, `media_dir_id`, `device_id`, `csrf_token` never count;
  a multi-valued key counts once.
- **Pagination accessible names changed**: each `.page-btn` now carries an
  `aria-label` ("First"/"Previous"/"Next"/"Last") which overrides its visible
  "Next ›" text, and at ≤640px `.page-btn-label` is `display: none` entirely.
  `getByRole('link', { name: 'Next ›' })` no longer resolves — use `'Next'`.
- Built-in themes are exercised by swapping the `link[href*="/theme.css"]` href
  (route `/themes/<slug>/theme.css`) **with a cache-busting query param** — the
  `load` event never fires if you assign the href the page already has — and
  setting `data-theme` on `<html>`. Do NOT flip the app-wide default theme from
  this file; that is a global setting other suites read.
- Filter-config touch reorder: rows are `.filter-config-row[data-key]`, handle
  `.filter-config-handle` (44×44 only under a coarse pointer). The handler
  ignores `pointerType === 'mouse'`, so only `touchDrag` (CDP) moves a row.

### Timeline scrubber alternative (new)
- `.timeline-scrubber` (the drag rail) is hidden at `max-width: 900px` **or** any
  coarse pointer; `.timeline-jump` — a sticky, horizontally scrollable row of
  `.timeline-jump-year` links — takes over. It is plain markup in `index.html`,
  so it also works with JavaScript disabled.
- `.photo-gallery` declares `--timeline-jump-height: 52px` in that media query;
  `.timeline-day-header`'s sticky `top` and `.timeline-month-divider`'s
  `scroll-margin-top` both add it to `--navbar-height`. Assert the header's
  sticky `top` only after scrolling, and compare it against the *stuck* jump
  bar's box — before any scroll the bar is still in flow further down the page.
- Year-mark hrefs come from `_timeline_index` in `yaffo/routes/home.py`. They now
  carry `page-size`: without it a link computed for `page-size=10` landed on a
  page the server rendered with 25 per page, and the `#month-YYYY-MM` anchor
  pointed at nothing. The JS drag path never had this bug (it builds its URL from
  `window.location.href`).

### Coarse-pointer facts
- `.photo-hover` (the card's hover detail overlay) is `display: none` under
  `(hover: none), (pointer: coarse)`; the card's tap opens the detail page.
- `.photo-card .favorite-toggle` is `opacity: 0` until card hover — on touch that
  made the only grid favourite control invisible. It is now opaque and 44×44
  under a coarse pointer. Tapping it toggles server-side state, so a test must
  tap twice to leave the library as it found it.
