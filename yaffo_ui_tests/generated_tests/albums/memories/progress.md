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

## Responsive Coverage (added 2026-08-30, P2 albums rollout)

- Ten responsive scenarios were added by hand (no generator, no healer) alongside
  the nine behaviour scenarios; the file is 19 tests and passed 19/19 with
  `npm run test:spec -- generated_tests/albums/albums.spec.ts --port 5202`.
- Shared contract assertions are imported from `generated_tests/_support/responsive.ts`
  (`CONTRACT_WIDTHS`, `VIEWPORTS`, `expectRouteFits`, `expectNoPageOverflow`,
  `expectFitsViewport`, `expectPanelContract`, `withTouchContext`, `touchDrag`).
  Do not re-implement an overflow or panel check locally — that forks the contract.

### Panels this family registers

- `#albums-nav` (toggle `#albums-nav-toggle`) — the album list, on every
  `albums/_base.html` screen: the overview and every album detail/edit page.
- `#album-add-filters` (toggle `#album-add-filters-toggle`) — the bulk-add filter
  sidebar, from `_sidebar.html` with `panel_prefix='album-add'`. There is no
  `album-add-actions` panel: the add screen renders no actions block.
- **Below 1200 px a registered panel is `hidden` until its peer button is
  pressed.** `openAlbum()` clicks the sidebar link and therefore only works at
  desktop width; narrow tests must use `seededAlbumPath()`, which reads the href
  off an `.album-tile` on the overview (tiles render at every width).
- The applied-filter badge `#album-add-filters-toggle [data-nav-panel-count]` is
  server-rendered: it is `hidden` with no filters and shows `1` for `?year=<y>`.

### Reorder: drag is not a touch path

- `albums.js` reorders with the **HTML5 drag-and-drop API** (`draggable`,
  `dragstart`/`dragover`/`dragend`). A real emulated touch stream emits none of
  those events, so `touchDrag()` from the shared helper provably does **not**
  reorder — asserted deliberately in
  `albums_touch_drag_alone_never_reorders_the_album`.
- The touch-safe path is the ↑/↓ `.album-reorder-button` pair that `initReorder`
  injects into every card in edit mode (`aria-label` from the grid's
  `data-move-earlier` / `data-move-later`). Each press reorders the DOM and POSTs
  to `/reorder` (204).
- `responsive.css` (shared owner) hides `.album-reorder-controls` unless
  `:focus-within` or a coarse pointer, and sizes the buttons at 32 px.
  `albums/albums.css` now overrides both **for album edit mode only**: the
  controls are displayed whenever `.photo-grid.is-selecting`, and the buttons are
  44×44. Tests assert both.

### Layout facts and the two regressions fixed here

- A long unbreakable album name (`Sommerferienfotografiesammlungs…`) used to size
  `.page-header`, then `.main-container`, then the document — the page scrolled
  sideways at **every** width, not just narrow ones.
- `.page-header-main { flex: 1 }` used a zero basis, so the album's five header
  actions won the whole row between roughly 700 and 1000 px and squeezed the
  title to ~58 px.
- Both exposed shared header defects. The integration fix now lives in
  `base.css`: titles wrap anywhere and the header's main column has a real flex
  basis. The temporary album-scoped copies were removed.
- At 640 px and below `responsive.css` stacks `.page-header-actions` children at
  `width: 100%`, so the album's five actions are five full-width rows (~225 px of
  header). Their height is 37 px, under the 44 px touch minimum — a shared
  `button.css`/`responsive.css` concern, not fixed here.
- `#album-selection` is `position: sticky; top: var(--navbar-height)`; nav.js
  publishes `--navbar-height` (57 px at 390 px). The edit page does not scroll at
  390×844 with four members, so assert the resolved sticky offset rather than
  scrolling.
- At 320 px `.modal-content` is a full-bleed sheet (x = 0, width = viewport) with
  `.modal-body` as the `overflow-y: auto` scroll region. `expectFitsViewport`
  checks **both** axes, so scroll a below-the-fold element (pagination) into view
  before calling it.

### Shared pagination accessible name changed

- The shared pagination links now carry `aria-label` (so they still name
  themselves when they render icon-only at ≤640 px). The accessible name is
  therefore `Next` / `First`, **not** the visible `Next ›` / `« First`.
  `albums_selection_survives_pagination` was updated to
  `getByRole('link', { name: 'Next', exact: true })`; the old locator timed out.

### State across a resize

- Edit-mode selection is URL state (`select_id`), so it survives a viewport
  change with no navigation — assert the URL is byte-identical afterwards.
- A year chosen in the add screen's Filters panel survives the panel being moved
  back into the page on desktop, because nav.js moves the live form DOM. Both the
  native `select#year-select` value and the `.searchable-select-display` text
  persist.
