# organize-review/faces-and-people

Shot: people-list.webp (People table, clip .main-content).

## Observed runs
- Run: 23662 px differ (1.1125%), bbox 1798x428 at (675,266) device px.
  Diff is pure horizontal glyph ghosting across the table header and every row
  cell from GENDER through ACTIONS; the NAME column, the "People" title and the
  "Add Person" button are unchanged. Content identical (Elena/Marcus/Maya/Theo
  Bennett, genders, birthdates, FACES=10, PHOTOS=10, Edit/Delete links).
  No dependency hash changes -> not a code-driven UI update; looks like
  font metric / column-width rendering variance. Treated as
  environment_instability, quarantined (>0.1% pixels, whole-table layout drift).
  Prose on the page unaffected.
