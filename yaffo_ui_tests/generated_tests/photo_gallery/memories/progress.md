# Photo Gallery Tests — Current State (2026-07-01)

## Status: PASSING (3/3) against the isolated sandbox

## Application facts (verified against live app)
- Gallery is the home page `/`. Container: `.photo-grid`; items: `.photo-card`; card date: `.photo-date`; hover overlay: `.photo-hover`.
- Photo cards open details at `/media/view/{id}` (photos→media rename). Images served from `/media/{id}` with `data-fallback` = `/placeholder`.
- Seeded test data: 14 photos, multiple years, so page-size 10 yields 2 pages.
- Pagination component: `.page-btn` anchors named `« First`, `‹ Previous`, `Next ›`, `Last »`; disabled state = `.disabled` class + `onclick="return false"`.

## Critical gotcha: searchable-select widgets
Every `<select class="searchable-select">` (`#year-select`, `#page-size`, etc.) is hidden (`display:none`) behind a custom widget inserted immediately after it. `locator.selectOption()` TIMES OUT on these. Interact via:

```ts
const wrapper = page.locator('select#year-select + .searchable-select-wrapper');
await wrapper.locator('.searchable-select-display').click();
await wrapper.locator('.searchable-select-option').filter({ hasText: value }).first().click();
```

- `#page-size` option VALUES are full URLs (`/?page=1&page-size=10&...`); choosing one navigates via the select's onchange. Option text has surrounding whitespace — match with `/^\s*10\s*$/` to avoid matching "100".
- The filter form GET-submits every filter field, so don't assert exact query strings; use regexes like `page.waitForURL(/[?&]year=2014/)`.

## History of resolved issues (do not re-investigate)
- `.gallery-grid` selector bug → fixed long ago (`.photo-grid` is correct).
- 2026-07: `selectOption` timeouts on year/page-size selects → caused by the searchable-select migration, fixed as above.
