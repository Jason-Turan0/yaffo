# organize-review/assigning-faces — shot notes

## faces-assign-02-controls.webp (clip .sidebar-container)
- 2026 run: recapture 624x2992 -> 624x3006 device px = +7 CSS px of sidebar frame
  height (1496 -> 1503 CSS). 54,334 px differ (~2.9%), concentrated in the Filters
  card below the "Group by" row, where labels/values render doubled by a small
  vertical offset — i.e. the lower part of the sidebar reflowed with the frame.
- Content is identical: same Actions panel (Assign to Person, Assign Selected,
  Ignore Selected, Create Person, KEYBOARD SHORTCUTS 1-4 Elena/Marcus/Maya/Theo,
  Enter / i or 0, gear, "Press ? for help") and same Filters values (All Years,
  All Months, Any Person, Similarity, 50%, 2,000, Apply/Clear Filters).
- `assigning-faces.lock.json` dependency hashes unchanged (sidebar.css, faces/index.css,
  templates/faces/index.html, etc.), so this cannot be a product change.
- Same +7 CSS px frame growth as the recorded `organize-review/locations` incident
  (1596 -> 1603 CSS). Treat as viewport/height rounding drift -> quarantine; do not
  promote a reframed shot whose content moved.
- If the 7 px growth recurs every run, the sidebar height is not reproducible
  (100vh / sticky rounding) and the crop strategy needs a fixed height instead.
- Page prose unaffected; no sentences needed edits.
