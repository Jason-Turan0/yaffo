# Remove Duplicates Tests — Current State (2026-07-02)

## Status
- Generated from `yaffo_ui_tests/specs/remove_duplicates.yaml`; 4/4 green; typecheck
  passes. Serial: one find_duplicates job feeds all four scenarios.

## Corpus Design (the key insight)
- Grouping is by **exact perceptual hash** (`imagehash.phash`, see
  `background_tasks/tasks/find_duplicates.py`) — NOT content hash. Copies of one
  image with different appended bytes all collapse into ONE group.
- The corpus is generated per run: `../venv/bin/python -c` with PIL renders
  GROUP_COUNT (12) distinct random-noise 64×64 JPEGs (`dup-<g>-a.jpg`), each copied
  to `dup-<g>-b.jpg` → exactly 12 groups of 2. 12 > page size 10 exercises pagination.
- Everything lives in scratch dirs under the sandbox root (from Settings' Database
  Path): `spec-dup-scan-<uniq>` and `spec-dup-dest-<uniq>`; `afterAll` rm -rf's both.
  The seeded library is never scanned.

## Flow / Selector Map
- Fresh page: `#directories-container` shows "No directory selected"; click
  `#add-directory-button` (HTMX swap of `#remove-duplicates-form`) to add a row.
- `input[name="directory"]` rescans on `hx-trigger="change"` — **dispatch the change
  event explicitly** (`locator.dispatchEvent('change')`); fill+blur is flaky under
  automation. Count lands in the "Total Media" `.stat-card`.
- `#find-duplicates-button` POSTs `/utilities/remove-duplicates/start` (202) and JS
  reloads the page → the response body is unreadable. Get the job id by **diffing**
  `#job-progress-section [id^="job-"]` card ids before/after the click: COMPLETED
  find_duplicates jobs from prior runs stay listed on this page, so `.last()` grabs
  stale jobs (this exact bug produced "File not found" removals in an earlier run).
- Results page `/utilities/remove-duplicates/results/<job_id>`: `#duplicates-form`,
  header stats in `#duplicates-header` (`Total Media Processed`, `Duplicate Groups
  Found`, `Duplicates Selected`); poll the page until groups are all in (hashing runs
  behind the shared worker).
- Groups: `.duplicate-group` → `.photo-card` tiles; first = kept (no `.selected`),
  rest `.selected`. Clicking a card HTMX-toggles it (`hx-target` itself) and
  OOB-swaps the header; selected count updates accordingly.
- Pagination: `.page-navigation .page-btn` (First/Previous/Next/Last), `.page-info`
  "Page X of Y", `.results-count`. Selections ride hidden `selected_photo` inputs in
  the form → they survive page changes.
- Action select `#action-type` (searchable-select): trash | moveFolder | delete.
  moveFolder reveals `#destination-folder` (+ Browse). Values round-trip through the
  server on header re-render.
- Execute: `.btn-danger` "Remove Selected Duplicates" posts `/execute/<job_id>`;
  response sets HX-Trigger toast + HX-Redirect to the utility page. **The toast is
  wiped by the redirect — don't assert it.** Verify: URL back at the utility page and
  the marked files physically in the destination dir (fs.readdirSync), one kept copy
  per group left in the scan dir.

## Known App Gap (flagged, not fixed)
- Executing the removal — including "Permanently Delete" — has **no confirmation
  dialog**; the button posts immediately. The spec expected one. Treat a future
  confirm-dialog addition as the app catching up to the spec; the test asserts the
  current direct-post flow and says so in a comment.
