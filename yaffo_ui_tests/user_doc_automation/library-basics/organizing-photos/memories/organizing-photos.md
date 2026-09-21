# organizing-photos (library-basics/organizing-photos)

Hub page, no screenshots. Its walkthrough only visits surfaces to keep them in the
dependency set, so prose (not shots) is what gets reviewed on dependency changes.

Notes:
- Duplicate review section must describe the duplicate scan as covering photos AND
  videos; the reference page (organize-review/duplicates.md) and the
  remove_duplicates.html header say "duplicate photos and videos".
- The page links to ../organize-review/duplicates.md for duplicates details.
- CSS-only dependency changes (e.g. static/base.css, static/sidebar.css,
  static/locations/list.css) are styling/robustness refactors
  ([hidden] override, scoping `.filter-group > label` to the direct child,
  page-header flex basis, /locations responsive + coarse-pointer rules). They
  change no control name, count, or behaviour this hub page describes, so a
  dependency-fingerprint flag on those files needs no prose edit.
- The walkthrough's `.nav-page-tab` selector (templates/base.html nav_pages strip)
  still exists; it is defined by static/pages/nav.css, not by the sheets above.
