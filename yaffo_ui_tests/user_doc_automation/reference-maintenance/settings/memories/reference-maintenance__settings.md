# reference-maintenance/settings

## Shot
- `settings-overview.webp`: viewport 1400x2200, goto /settings, clip `.main-content`,
  ignoreRegions `.media-dir-path`, `#current-thumbnail-dir`, `#thumbnail-size`.
  setup waits for `#thumbnail-size` to leave "Counting…" (NDJSON stats stream).

## Known facts
- This page's recorded dependencies are the *shared chrome* loaded by
  `templates/base.html` (base.css, sidebar.css, notification.css,
  components/{modal,overlay,selection_bar,tooltip,icons,file_browser}.css,
  nav.js, selection_bar.js, settings/index.css, base.html). Any change to those
  files flags this page even when nothing on the Settings screen moved, because
  the navbar/modals live outside the `.main-content` clip.
- 2026-09: flagged with a dependency-only change-set (shared chrome CSS/template),
  no screenshot change. Checked the Settings template(s) against the prose:
  section order Language, Units, Media Directories, Thumbnail Directory,
  AI Generation, Photo Labels, System Information — unchanged; control labels
  ("Application language", "Preferred distance unit", "Add Directory"/"Browse…",
  "Remove", "Model", "Add label", "Re-classify all photos") unchanged. No doc edit
  warranted (empty diff). Do NOT invent changes for shared-CSS-only flags.

## App surface (authoritative)
- `yaffo/templates/settings/index.html` + `_llm.html` + `_llm_api_key.html`
  + `_labels.html` render every section. A *new* `settings-section` div here is a
  real gap in the guide's obligation ("every section, in order").
