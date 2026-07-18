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
