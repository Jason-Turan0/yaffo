# Albums Tests — Current State (2026-07-18)

## Status

- Generated from `yaffo_ui_tests/specs/albums.yaml`.
- TypeScript validation passes with `npx tsc --noEmit`.
- Playwright runtime passes against a fresh isolated runner at
  `http://127.0.0.1:5002`: 9/9 tests passed on 2026-07-18.
- The suite targets the standard single-instance environment. Album sharing with
  a paired peer remains in the two-instance Sharing suite; Albums only verifies
  the no-paired-devices modal state.

## Seeded Data and Ordering

- The isolated runner seeds `Seeded Album` with four photo members and the library
  contains 16 media items total (14 photos and two videos).
- Album tests mutate shared server state, so `test.describe.configure({ mode:
  'default' })` opts the file out of the repository's `fullyParallel` behavior and
  keeps tests in file order on one worker.
- Tests restore created albums, names, membership, and order. Setting the seeded
  album cover is intentionally retained because no later scenario depends on the
  original cover.

## Routes and Stable Selectors

- Overview: `/albums`; tiles are `.album-tile`, sidebar links are
  `.albums-sidebar .panel-nav a`, and count chips use `.chip`.
- Album detail: `/albums/<id>`; members are
  `#album-grid .photo-card[data-select-id]` and the header member count is in
  `.page-header .subtitle`.
- Add screen: `/albums/<id>/add`; the filter form is `#filter-form`, grid is
  `#add-grid.is-selecting`, selection bar is `#add-selection`, and submit button
  is `#add-to-album-button`.
- Edit mode is `?edit=1`; selection controls are in `#album-selection`, cover uses
  `#set-cover-button`, and removal uses `#remove-from-album-button`.
- Create/edit/share modals are `#newAlbumModal`, `#editAlbumModal`, and
  `#shareAlbumModal`.
- Album delete and member removal use the global application confirm dialog:
  `#global-confirm-dialog.active` with `#confirm-dialog-confirm`. Do not use a
  native dialog handler.

## Selection Semantics

- Selection is URL-backed. Individual choices use repeated `select_id`; selecting
  the entire scope uses `select=all`, and unticked exceptions use `exclude_id`.
- The add button exposes the full filtered scope through `data-match-count`. This
  is more reliable than visible-card counts and proves bulk selection applies to
  every matching page.
- On the add screen, successful submission stays on `/add`, appends `added=N`,
  displays `.add-photos-confirmation`, and removes newly added members from the
  candidate grid.
- Existing album members are excluded from add-screen match counts. Cleanup should
  compare member IDs before and after a scenario, then remove only the delta.

## Filters, Cover, and Reorder

- Filter selects are enhanced by the searchable-select component. The hidden
  native select is followed by `.searchable-select-wrapper`; choose an option via
  `.searchable-select-display` and `.searchable-select-option`, then click Apply
  Filters.
- Set as cover is enabled only for exactly one selected member. The chosen card
  receives `.album-cover-chip`, and the overview tile image URL contains
  `/media/<member-id>`.
- Reordering is plain HTML5 drag handling in `static/albums/albums.js`: dispatch
  `dragstart` on the source, `dragover` on the target, and `dragend` on the source.
  The final event posts to `/reorder` and returns 204. Restore the original order
  with repeated `media_item_id` form fields.

## No-Peer Sharing State

- In the single-instance sandbox the Share modal contains no
  `input[name="device_id"]` controls.
- Its `.no-data` message directs the user to pair a device on the Sharing tab.
- No overview tile should display a `Shared` chip in this environment.
