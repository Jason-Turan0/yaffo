# library-basics/indexing-library — review notes

## CSS-only dependency churn (no shot change)
- Run flagged `yaffo/static/base.css` + `yaffo/static/sidebar.css` as changed while
  `shots[...].sha256` was unchanged. Both are globally loaded and recorded in
  `indexing-library.lock.json` for every page that extends `base.html`.
- The clipped shot is `.utility-page`, which lives inside `.utilities-content` — it
  excludes the utility sidebars, so `sidebar.css` edits (`.sidebar` sticky/top,
  `.filter-group > label` scoping) cannot move a pixel in this shot.
- `base.css` edits (form-control font/colour inheritance, `.page-header-main`
  flex-basis, `overflow-wrap` on the header, `[hidden] { display:none !important }`)
  are cosmetic/robustness fixes; none rename a control or change a count, so the
  page prose still holds.
- Verdict for this class: promote / no editorial change — do NOT rewrite prose for
  a stylesheet fingerprint.

## Prose anchors verified against code
- Counts on the page come from `templates/utilities/index_photos.html`; labels match
  ("Total on Filesystem", "Imported in Database", "Indexed in Database",
  "Not Indexed", "Orphaned in DB").
- Buttons "Sync Database" (#sync-button, hidden until work) and "Reindex Library"
  (#reindex-button, confirm-dialog then destructive) match `static/utilities/index_photos.js`.
- "Supported Media" matches `yaffo/common.py`: PHOTO {.jpg .jpeg .png .heic},
  VIDEO {.mp4 .mov .m4v .avi .mkv .wmv .flv}, PLAYABLE_VIDEO {.mp4 .mov .m4v}.
