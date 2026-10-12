# organize-review/faces-and-people

## people-list.webp
- 2026 run: recapture differed by ~1.1% of pixels, bounded to the region right of
  the left-aligned Name column and spanning the whole table (headers included).
- Cause: the Faces/Photos counts are now emphasized — `yaffo/static/people/list.css`
  styles `.stat-number` with `font-variant-numeric: tabular-nums; font-weight: 600`
  (the stale lock still records that file as empty). The wider digits move the
  auto-layout column boundaries by a pixel or two, so every cell from Gender
  rightwards re-renders while the Name column and page title stay pixel-identical.
- Verdict: expected UI emphasis change, not a capture defect or regression.
  Content identical: four people (Elena/Marcus/Maya/Theo Bennett), same genders,
  same birthdates, same 10 faces / 10 photos each.
- Page prose was unaffected by this change; no sentences needed edits.

## 2026 run — dependency-only flag (no screenshot diff)
- Flag: "no screenshot changed, but dependency fingerprints changed" for
  `yaffo/static/base.css` and `yaffo/static/sidebar.css`. Both are shared global
  stylesheets loaded on every page, so a fingerprint change there is usually a
  lock-staleness artifact (same shape as the `people/list.css` false alarm above:
  the file had already been recorded as stale/older content).
- Because the recapture is pixel-identical to the committed shot, no visible
  content, count, label, or layout changed; nothing in the prose can be
  contradicted. No edits made; the fingerprints should simply be re-recorded.
- Reminder: this page has no shot of `/people/:id/faces`, so CSS that only affects
  the person-faces sidebar (e.g. `similarity_filter`) cannot show up in the diff —
  check those surfaces by reading the CSS, not by trusting the pixel result.

## 2026 run — faces-review.webp grew 29 CSS px taller, content identical
- Reported as a reframe: 2784x3006 became 2784x3064, "161472 pixels differ
  after top-left alignment". 2784 x 58 = 161,472 exactly, i.e. every changed
  pixel is in the 58 added device rows at the bottom; the whole overlap is
  pixel-identical (same cluster, "54 of 54 unassigned faces", 50% threshold,
  same sidebar controls and pager).
- Cause: the Pages strip toggle moved from per-browser localStorage to the
  server-side `show_pages_bar` setting (`routes/pages.py` PUT
  /pages/bar-visibility + `get_show_pages_bar`; `base.html` renders the strip
  with `hidden` and the toggle `collapsed`; `nav.js` now does `apply(bar.hidden)`
  and enables the button during an async save). A hidden strip makes the sticky
  navbar ~29px shorter, so the `.main-container-layout` crop — whose height is
  `max(content, calc(100dvh - 100px))` in `base.css` — starts higher and ends
  29px lower, showing plain page background.
- Do not chase this as a content change: before believing any diff on this shot,
  subtract the added-row area (width x height delta) from the differing-pixel
  count. If they are equal, only the crop height moved.
- Page prose unaffected: the strip is not mentioned anywhere on this page, and
  the Faces surface itself is unchanged.