# Index Photos Tests — Current State (2026-07-02)

## Status
- Generated from `yaffo_ui_tests/specs/index_photos.yaml`; green solo and in the full
  parallel run; typecheck passes. Serial, long budgets (per-test 900s) because the
  import waits behind the shared taskq worker.

## Page Mechanics
- Shell renders instantly; scan streams NDJSON from `/utilities/index-photos/scan`.
  Stats start as `—`: progress records tick `#stat-total-filesystem`; the final done
  record fills `#stat-total-imported`, `#stat-total-indexed`, `#stat-unindexed`,
  `#stat-orphaned`, renders `#scan-results`, and reveals `#sync-button` when work
  exists. Results are either `.empty-state` "Everything is in sync" or `.section`
  tables titled "Unindexed Photos" / "Orphaned Database Entries" (orphan reason label:
  "File deleted from disk").
- Sync: POST `/utilities/index-photos/sync` with the scan-held lists → 202 → JS toast
  "Sync job started" + reload. Import/index runs as background jobs.

## Test Strategy (real filesystem round-trip)
- The Flask server and test process share a filesystem — use `node:fs`.
- Media dir path comes from `/settings` (`.media-dir-item .media-dir-path`).
- Copy an existing JPEG to `spec-index-<uniq>.jpg` and **append marker bytes** so
  content hashing can't treat it as a duplicate of the source.
- Import wait: `toPass` loop re-loading the page until unindexed+orphaned are 0,
  budget 360s — parallel suites (labels reclassify-all) queue minutes of CLIP work
  ahead of the import on the single shared worker.
- After the counters settle, assert the in-sync UI on **one more clean page load**:
  a load that races the finishing import can reveal `#sync-button`, and the reveal is
  one-way per document.
- Then delete the file → orphaned row (reason + path in table) → sync again →
  baseline restored. `afterAll` unlinks the file if a test died mid-flight.

## Environment Facts
- The isolated environment (isolated_runner.ts) starts Flask + taskq host but **no
  filesystem watcher** (the watcher only runs under `python -m yaffo` YAFFO_ROLE
  dispatch), so dropped files are not auto-imported.
- **Do not** exercise the "No Media Directories Configured" empty state: removing the
  seeded media dir while the hourly `file_sync` automation could tick deletes every
  media row as "unconfigured" and destroys the sandbox for all parallel suites.

## App Bug Found & Fixed (2026-07-02)
- `#sync-button` renders with the `hidden` attribute, but author CSS
  (`.btn { display: inline-block }` in button.css and
  `button[data-icon] { display: inline-flex }` in icons.css) outranked the UA's
  `[hidden] { display: none }`, so the button was ALWAYS visible. Fixed globally in
  `static/base.css`: `[hidden] { display: none !important; }` (safe: all visibility
  toggling in the app drives the attribute itself; no CSS anywhere styles [hidden]
  visible). If the Sync-button-hidden assertion regresses, check that reset first.
