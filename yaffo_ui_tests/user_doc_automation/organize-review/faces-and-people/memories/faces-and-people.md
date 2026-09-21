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
