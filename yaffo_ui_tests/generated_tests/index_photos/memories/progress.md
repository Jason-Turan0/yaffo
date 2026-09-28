# Library Health Tests — Current State (2026-09-27)

## Status
- Rewritten by hand from `yaffo_ui_tests/specs/index_photos.yaml` after the page
  became **Library Health** (`/library/health`, was Index Photos at
  `/utilities/index-photos`; nav "Library"). 10 tests, green solo
  (`npm run test:spec -- generated_tests/index_photos/index_photos.spec.ts`), ~13s.
- Every test must fit the **30-second** per-test budget
  (`lib/services/test_timeout_policy.ts`; explicit timeouts ≤ 30s, no overrides).
  Multi-job flows are split into serial tests that each wait on one background job.

## Page Mechanics
- Shell renders instantly; scan streams NDJSON from `/library/health/scan`. Stats
  start as `—`; the done record fills `#stat-total-imported`, `#stat-total-indexed`,
  `#stat-unindexed`, `#stat-orphaned`, `#stat-missing-thumbnails` (stays `—` only when
  the thumbnail folder can't be checked — never in the sandbox).
- **Library status** section: one `.issue-card` per problem, hidden until it applies:
  `#issue-unindexed` (fix `#index-new-button` "Index them"), `#issue-orphaned`
  (`#remove-orphaned-button` "Remove them"), `#issue-missing-thumbnails`
  (`#regenerate-thumbnails-button`), and the server-rendered `#issue-failed`
  (`#retry-failed-button` "Retry all", shown on load when files failed). Titles in
  `#issue-<key>-title`; file tables in `#issue-<key>-files` inside a closed
  `<details>` ("Show files" — open it before asserting rows are visible).
- `#status-in-sync` ("Everything is in sync") shows only when no card and no warning
  (`#scan-warnings`: empty media folder, unavailable thumbnail folder) applies.
- Index them / Remove them both POST `/library/health/sync`, each with only its own
  list (`files_to_index` or `files_to_delete`). Regenerate POSTs
  `/library/health/regenerate-thumbnails`. All → 202 → toast + reload. Fix buttons are
  disabled while an import/index job runs, so `clickFix` reloads until enabled.
- A finished thumbnail repair's run-history row reads "Regenerated N thumbnails". The
  files land a moment before the job is finalized, so poll the history after the
  missing count reaches 0 (until then the run is a job card, not a history row).

## Test Strategy (real filesystem round-trip)
- Server and tests share a filesystem; file setup only through `_support/sandbox-fs`
  (generated code may not import fs). Media dir from `/settings`
  (`.media-dir-item .media-dir-path`), thumbnail dir from `#current-thumbnail-dir`.
- New photos: `copyPhotoWithUniqueMarker` (keeps the source extension; marker bytes
  defeat duplicate detection).
- Fix flow: drop A → Index them; delete A + drop B → both cards → Index them (orphan
  stays) → Remove them; delete B → Remove them → in sync. `afterAll` unlinks anything
  left and deletes the test person.
- Missing thumbnail: create a person (`/api/people/create`), assign the tail of the
  unassigned pool (skipping the face_assignment suite's active similarity cluster)
  via `/api/faces/assign`, fetch `/faces/<id>`, find the crop by content
  (`findFileWithContentIn`), delete it (`/faces/<id>` → 404), then regenerate and
  check it's 200 again and the face is still on `/people/<id>/faces`.

## Environment Facts
- The isolated environment starts Flask + taskq host but **no filesystem watcher**,
  so dropped files are not auto-imported.
- **Do not** exercise the "No Media Directories Configured" empty state: removing the
  seeded media dir while the hourly `file_sync` automation could tick deletes every
  media row as "unconfigured" and destroys the sandbox for all parallel suites.
- `[hidden] { display: none !important; }` in `static/base.css` is what keeps hidden
  cards/buttons hidden against author `display` rules (a 2026-07-02 bug fix). If a
  hidden-card assertion regresses, check that reset first.
- To stage a file that couldn't be indexed, set a row's status to FAILED with
  index_error/index_error_detail/index_failed_signature in setup (direct DB write is
  fine in setup, per the no-routing-around-bugs rule). The scan skips FAILED files
  whose size:mtime is unchanged, so they're not in the unindexed count.
