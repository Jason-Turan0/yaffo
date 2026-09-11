# Face Assignment Tests — Current State (2026-07-01)

## Status: PASSING (6/6) against the isolated sandbox (verified twice in a row — cleanup restores the shared face pool)

## Page architecture (redesigned — old notes about a flat face grid are obsolete)
- `/faces` renders `.suggestion-group` clusters server-side, but the `.face[data-face-id]` thumbnails are painted CLIENT-SIDE into the ACTIVE group's `.grid` only. One cluster is visible at a time; the rest carry the `hidden` attribute.
- Each group's complete face list is in its `data-faces` JSON attribute: `[{id, photo_date, similarity}]`. Assert on this payload for hidden groups or whole-cluster properties.
- All faces of the active cluster start SELECTED. Deselect all via the group's `.cluster-select-all` chip (a toggle whose label names its next action: 'Clear selection' when everything is selected, 'Select all N faces' otherwise); click individual faces to toggle. There is NO `#deselect-all` button anymore.
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

## 2026-08-30 — responsive rollout (P3: faces and people)

### Status: PASSING (18/18) — 6 pre-existing behaviour tests plus a 12-test `Face Assignment — responsive` describe

Hand-written, not generated and not healed. Shared assertions are imported from
`generated_tests/_support/responsive.ts`; re-implementing an overflow or panel
check there would fork the contract.

### Panel contract on this page
- `/faces` registers TWO panels through `_sidebar.html`: `#faces-actions` and
  `#faces-filters`, with peer buttons `#faces-actions-toggle` and
  `#faces-filters-toggle`. Actions is declared first (it acts on the current
  selection). `expectPanelContract` works against either.
- **Gotcha:** the Menu button is `#nav-menu-toggle` and does NOT carry
  `data-nav-panel-toggle`. An ordering assertion has to select
  `'[data-nav-panel-toggle], #nav-menu-toggle'` — querying only the former
  silently drops Menu and the "Menu sorts last" check passes vacuously.
- The applied-filter badge is `#faces-filters-toggle [data-nav-panel-count]` and
  is server-rendered. `applied_filter_count` counts `group_by` and `threshold`,
  so `/faces?group_by=similarity&threshold=2` shows `2`.
- Panels are moved, not re-rendered, so `#threshold-range` and a half-typed
  `#create-person-name` both survive a resize from 390 to 1440 without a reload.
- Escape closes the open panel; `nav.js` steps aside while a `.modal.active` is
  up, which is why the shortcut dialog can be opened from inside the Actions
  panel and dismissed on its own.

### Bugs found and fixed (each has a scenario naming the cause)
- **iPhone SE navbar.** At 375px the localized Actions, Filters and Menu labels
  could make the top controls wrap or overflow. At ≤400px those controls now
  render as 44px icon buttons; explicit `aria-label` values retain their names,
  and the applied-filter count is positioned inside the Filters target.
- **Cluster pager.** `templates/faces/index.html` rendered five full-text buttons
  (`« First`, `‹ Previous`, …) that could not share a row at 320px, so the footer
  widened the page. They now use the shared pagination markup — `data-icon` plus
  a `.page-btn-label` span — which `components/pagination.css` collapses to 44px
  icon controls at ≤640px.
- **Shortcut reordering.** The rows used HTML5 drag-and-drop (`draggable="true"`,
  `dragstart`/`dragover`). Touch produces none of those events, so the handle was
  decorative on a phone. It is Pointer Events now, with the pointer captured on
  the LIST (`#shortcut-config-list`) rather than on the moving row — capture on
  the row ends the stream the moment the row is re-inserted. Each row also has
  explicit `.shortcut-config-move-btn[data-move="up"|"down"]` controls, disabled
  at the ends of the list.
- **`.main-content { overflow-y: auto }`** in `faces/index.css` made the column an
  implicit horizontal scroller, hiding containment failures. Replaced with
  `min-width: 0`, which is what actually stops a wide cluster from setting the
  page width (a flex item's automatic minimum is its content size).

### Testing notes
- `touchDrag(context, page, from, to)` from the shared helper dispatches a real
  CDP touch stream. It only works inside `withTouchContext` (`hasTouch`,
  `isMobile`). `.filter-config-handle` already carries `touch-action: none`, so
  the drag does not turn into a scroll.
- In a touch context use `.tap()`, not `.click()`, for the panel toggle and the
  `#configure-shortcuts-btn` — `click()` works but `tap()` is what the contract
  is actually about.
- The help dialog opens with `page.keyboard.press('?')`; the handler bails when
  focus is on an INPUT/TEXTAREA/SELECT, so press it straight after `goto`.
- `.face-preview-modal` and `.face-tooltip` are created once on init and live on
  `document.body`. At widths up to 640px, the explicit 44px preview button opens
  the modal; the face stylesheet pins it to all four viewport edges and centers
  it independently of the clicked face and document scroll. Above 640px, touch
  tablets open an anchored popover from the button and fine-pointer desktops
  open that popover on hover. Closing the phone modal restores focus to its
  opener, and neither preview path changes face selection.
- Previewing must NOT change the face's `selected` state — previewing and
  selecting remain separate actions.

### Unit-test trap (vitest)
`yaffo/static/faces/index.js` read `window.matchMedia` unguarded at init. jsdom
implements no `matchMedia` at all, so **all 20** `tests_js/faces/index.test.js`
cases were failing with `window.matchMedia is not a function` before this work —
it is not a Playwright-visible failure. The call is now guarded and falls back to
the fine-pointer branch. `tests_js/support/setup.js` still provides no
`matchMedia`, so `tests_js/pages/grid.test.js` (and any other module reading it)
fails the same way; that is a shared-setup gap, not a page bug.
