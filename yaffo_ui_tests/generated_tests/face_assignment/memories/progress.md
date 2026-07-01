# Face Assignment Tests — Current State (2026-07-01)

## Status: PASSING (6/6) against the isolated sandbox (verified twice in a row — cleanup restores the shared face pool)

## Page architecture (redesigned — old notes about a flat face grid are obsolete)
- `/faces` renders `.suggestion-group` clusters server-side, but the `.face[data-face-id]` thumbnails are painted CLIENT-SIDE into the ACTIVE group's `.grid` only. One cluster is visible at a time; the rest carry the `hidden` attribute.
- Each group's complete face list is in its `data-faces` JSON attribute: `[{id, photo_date, similarity}]`. Assert on this payload for hidden groups or whole-cluster properties.
- All faces of the active cluster start SELECTED. Deselect all via the group's `.group-select-checkbox`; click individual faces to toggle. There is NO `#deselect-all` button anymore.
- Assigning or skipping (`.skip-cluster-btn`) advances to the next cluster, or reloads the page for the next batch when none remain.
- Per-cluster pager (`.cluster-first/.cluster-prev/.cluster-next/.cluster-last`, `.sample-range`) pages through samples of 50 faces; there is NO page-size select / global pagination on this page anymore.
- Clusters come from DBSCAN with `min_samples=3` → every similarity group has ≥ 3 faces. People-mode groups are named after the person in `.cluster-name` and have `.assign-group-btn[data-person-id][data-person-name]` buttons; unmatched faces go to an "Unknown" group whose faces have `similarity: null`.
- Prefer URL params over driving the filter UI: `/faces?group_by=similarity&threshold=2` or `?group_by=people&threshold=2` (threshold 0–100 slider value; low = looser clusters).

## CRITICAL: assignment is asynchronous (taskq)
`POST /api/faces/assign` only sets faces to PROCESSING and enqueues a background task. The isolated sandbox MUST run the worker (`python -m yaffo.taskq.host` with the same `YAFFO_DATA_DIR`); `lib/services/isolated_runner.ts` spawns it since 2026-07-01. Tests must poll the person's `/people/{id}/faces` page for `[data-face-id="…"]` (expect().toPass) instead of assuming immediate completion — and must wait for completion before afterEach deletes the person, or the late task re-assigns faces to a deleted person and corrupts the pool.

## Other selectors / flows
- Sidebar person select `#sidebar-person-select` is a hidden searchable-select: click `#sidebar-person-select + .searchable-select-wrapper .searchable-select-display`, then the `.searchable-select-option` with the person's name. Toasts use `.notification.visible` (window.notification / window.showNotification — NOT Flask flash alerts).
- Quick create: `#create-person-name` + `#create-person-btn` → 201 from `/api/people/create`, success toast, then `window.location.reload()` after ~1.5s. The toast is wiped by the reload — assert on the repopulated `#sidebar-person-select option` instead.
- Keyboard shortcuts are rebuilt per cluster into `#sidebar-shortcut-people .shortcut-item` (`<kbd>N</kbd><span>Name</span>`); any person (even 0 faces) appears while ≤ 9 people exist.
- People list (`/people`): row link `a.person-name.row-link` → `/people/{id}/faces` (the old "View Faces" link is gone); delete via `a.action-link.delete` then the GLOBAL confirm dialog `#confirm-dialog-confirm` (the old `#deleteModal` is gone). Deleting a person unassigns their faces — this is how tests restore the shared pool.
- Subtitle: `.subtitle` shows "Showing X of Y unassigned faces".

## Test-data note
Hardcoded face-id sets from the dlib era (OBAMA_FACE_IDS etc.) are INVALID — the InsightFace reseed changed face ids. Don't assert specific face ids from memory; derive them from the page or `data-faces`.

## Concurrency
These tests share one server-side unassigned-face pool and create/delete people, so the describe block runs in `serial` mode with a 30s timeout (the global Playwright timeout is 5s).
