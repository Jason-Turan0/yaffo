# Settings Tests — Current State (2026-07-02)

## Status
- Generated from `yaffo_ui_tests/specs/settings.yaml`; 7/7 green against the isolated
  runner; typecheck passes. Serial suite — it mutates global settings and restores
  each one (finally blocks).

## Selector Map / Behaviors
- Sections live on `/settings`. Success/error toasts are `.notification.visible`.
- **Media dirs:** list `#media-dirs-list`, rows `.media-dir-item` (`.media-dir-path`,
  `[data-action="remove-media-dir"]`). Add: `#new-media-dir` +
  `[data-action="add-media-dir"]` → POST `/api/settings/media-dirs`. The server
  `mkdir -p`s the path, so any scratch path under the sandbox root works. Empty path
  → toast "Please enter a directory path". Remove confirms via
  `#global-confirm-dialog` (message names the dir) then DELETE
  `/api/settings/media-dirs/<index>`; toast "Removed: <dir>".
- **Sandbox root discovery:** read System Information's "Database Path:" `<code>`
  (`<root>/yaffo.db`) and take its dirname. Use **exact** text matching —
  "Database Path:" is a substring of "Task Queue Database Path:".
- **Language:** `#application-locale` native select hidden behind searchable-select
  (`.searchable-select-display` → `.searchable-select-option`). Save button is in
  `form[action$="/settings/locale"]`, **not** inside the select's `.form-group` (a
  parent-of-select locator finds nothing and hangs). POST redirects to /settings;
  assert `html[lang]` and translated text (es page header: "Ajustes" — server .mo
  translations exist for es/de/fr/hi/ar/zh). Restore by setting the hidden select's
  value via `evaluate` (label-independent) and submitting the same form.
- **Distance unit:** `#distance-unit` (mi/km; labels Miles/Kilometers), Save in
  `form[action$="/settings/distance-unit"]`. Persistence asserted via reload.
  Rendering elsewhere (locations/automation distance fields) intentionally not
  asserted — would couple suites.
- **Thumbnail dir:** current path in `#current-thumbnail-dir`; NDJSON stream fills
  `#thumbnail-count` / `#thumbnail-size` on load (assert they leave '…'/'Counting…').
  `[data-action="change-thumbnail-dir"]` fetches stats, then the global confirm
  dialog message contains "New location: <dir>" and "This will move N files (SIZE)".
  Confirm POSTs `/api/settings/thumbnail-dir` (moves files, rewrites Face/poster
  paths) and the page reloads. Test moves to `<current>-spec-<uniq>` and moves back
  in `finally`.
- **Labels vocabulary:** same `#labels-section` the labels suite covers extensively;
  here only a quick add/remove round-trip with a `spec-set-label-*` name (kept short
  to minimise interference with the labels suite's chip-count assertions when the
  whole suite runs in parallel).
- **System Information:** eight `.system-path-item` entries (Build Version/Timestamp,
  Database Path, Task Queue Database Path, ExifTool, FFmpeg, Image Classification
  Model, Face Recognition Model), each with a non-empty `<code>` ("Not found" counts
  as populated).

## Cross-Suite Hazards
- Locale changes are app-global; other suites assert English strings and their
  server-rendered pages (flash messages especially) render ONCE — a page rendered
  inside the Spanish window stays Spanish and fails any English assertion with no
  recovery. The language test therefore keeps the non-English window to a SINGLE
  render (assert on the redirect page only, restore immediately via
  page.request.post, no extra navigations inside the window) and verifies
  persistence-across-navigation afterwards with the restored locale (same
  read-per-request mechanism). Do not add navigations between the Save click and
  the restore POST.
- Never remove the seeded media directory; the "No media directories configured"
  empty state is unreachable without breaking other suites (documented, not asserted).
- The thumbnail move window briefly relocates face crop files; face_assignment
  asserts card visibility (not image bytes), so this is tolerated.
