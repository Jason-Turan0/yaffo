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
