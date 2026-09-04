# Locations Tests — Current State (2026-07-05)

## Status: PASSING (11/11) against the running sandbox at `http://127.0.0.1:5002`

Verified with:

```shell
cd yaffo_ui_tests && npx tsc --noEmit && BASE_URL=http://127.0.0.1:5002 npx playwright test generated_tests/locations/locations.spec.ts
```

## Application Facts

- `/locations` renders a canvas OpenLayers map into `#map`. Markers and clusters are not DOM nodes; use the exposed API for marker counts, coordinates, cluster composition, and selected IDs.
- The map API is `window.PHOTO_ORGANIZER.locations.map` with `{map, vectorSource, selectedPhotoIds, updateSelectionPanel, setClientFilter}`.
- Wait for the API to exist, `vectorSource.getFeatures().length > 0`, and a `rendercomplete` event before doing pixel math.
- Cluster features can be reached through the OpenLayers vector layer whose source features have a `features` property. Their style icon `src` is a data-URI SVG; partial clusters include a `<path>` sector.
- Current sandbox data has six located photos: one Tuscany sample, one Chicago/Grant Park sample, and four nearby White House photos. Tests derive expected IDs and counts from the live `vectorSource`.
- Use filename-based helpers when a scenario needs a specific map point:
  `featureIdByImageName`, `zoomToFeaturesByImageName`, and `selectFeatureByImageName`.
  This keeps tests independent of cluster ordering and current viewport state.

## Current UI

- The old `#popup` no longer exists. Clicking a marker or cluster opens `#selection-panel` with `.active`.
- The side panel renders `.preview-section`, `.preview-toggle`, `#photo-img`, optional `#preview-photo-select`, `.preview-thumb`, `#mass-location-input`, `#mass-assign-btn`, `.btn-clear-names`, `.selection-panel-close`, and quick assignment buttons.
- Selection state is held in `selectedPhotoIds`; zoom or pan rerenders clusters and the panel while preserving selected photo IDs.
- Empty plain map clicks clear selection. Shift-drag adds enclosed clusters to the current selection.
- The shared sidebar is client-side on this page. The unnamed checkbox is `input[name="unnamed"]`, not `#filter-unnamed`; Apply and Clear must not navigate away from `/locations`.
- The filter configuration modal saves with `data-page="locations"` via `/settings/filters/locations`.

## Mutation and Geocode Notes

- Always mock `/locations/reverse-geocode`; the real endpoint calls external Nominatim.
- The existing-nearby-name scenario should name only `whitehouse_2014_01282014.jpg`
  as `The White House`, then select `whitehouse_2014_03012014.jpg`. That proves a
  different nearby White House photo receives the existing-name recommendation
  without a reverse-geocode request.
- The same scenario then selects `obama-family-photo-celebration-1514413986.jpg`
  and expects the mocked reverse-geocode result, not `The White House`; this
  guards against using a distant existing name for Chicago.
- The recommendation lookup can re-render the panel after initial selection. In the custom assignment test, wait for `.btn-recommended` before filling `#mass-location-input`, then set the current DOM input and click `#mass-assign-btn` in one browser-context step to avoid the input being wiped by a late panel render.
- Assigning names POSTs `/locations/bulk-update` with `{media_item_ids, location_name}` and updates both `feature.get('name')` and `feature.get('item').name`.
- Clearing names POSTs `/locations/bulk-update` with `{media_item_ids, clear: true}`. The explicit `clear: true` flag is required; blank `location_name` is rejected by the server.
- The suite is serial because tests intentionally mutate location names. It clears all location names before mutation/geocode fixtures and clears again through the UI after assigning `Test Beach`.

---

# Responsive Rollout — P4 (2026-08-30)

## Status: PASSING (21/21)

```shell
cd yaffo_ui_tests && npm run test:spec -- generated_tests/locations/locations.spec.ts --port 5402
```

Ten responsive scenarios were added to `specs/locations.yaml` and hand-written into
this spec (not generated, not healed). They import `VIEWPORTS`,
`expectNoPageOverflow`, `expectFitsViewport`, `expectPanelContract` and
`withTouchContext` from `../_support/responsive`.

## Responsive layout facts

- Below **900 px** `#selection-panel` is restyled (in `static/locations/list.css`)
  into a centered, viewport-contained modal over a full-width map, with a
  backdrop and dialog semantics. It is the *same* DOM as the desktop side column
  — no separate modal component — which is why the selection, preview state and
  a half-typed location name all survive the breakpoint. Above 900 px it is
  `position: static` again, in the map's flex row.
- 900 px is the page's own boundary. The **panel/navbar** breakpoint is different:
  `nav.js` uses `(max-width: 1200px)`. A resize from desktop to narrow crosses
  both.
- Because the modal is `position: fixed`, `expectNoPageOverflow` deliberately
  skips it. Assert the modal itself with `expectFitsViewport(page,
  '#selection-panel')`, otherwise a modal hanging off-screen passes.
- The map's own scroll region is `#selection-panel-content`
  (`overflow-y: auto`, `overscroll-behavior: contain`). At narrow widths
  `.clusters-list` deliberately gives up its own 240 px scroller so there is one
  scroll region in the modal, not two.
- `@media (max-width: 900px), (hover: none)` carries the coarse-pointer rules.
  **Playwright does not emulate the `hover`/`pointer` media features** — a
  `withTouchContext` page still matches `(hover: hover)` — so the arm that the
  touch test actually exercises is the width one. Do not write a test that
  depends on `(hover: none)` matching.

## OpenLayers sizing — the waiting discipline

- OL caches its viewport size. `list.js` now calls `map.updateSize()`
  synchronously **and** on the next animation frame, from: a `ResizeObserver` on
  `#map`, `window` `resize`, `window` `orientationchange`,
  `visualViewport` `resize`, a `matchMedia('(max-width: 900px)')` `change`
  listener, and the panel's `transitionend`.
- The assertion that matters is `map.getSize()` matching `#map`'s
  `clientWidth`/`clientHeight` (±1). **Poll it** — see
  `expectMapSizedToContainer` in the spec. A bare read races the rAF.
- The desktop panel animates its width over 300 ms. Reading the panel box and the
  map box in two separate round-trips lands mid-transition and gives a false
  failure; read both inside one `page.evaluate` and poll that instead.
- `page.setViewportSize` does not reload, so a "no reload" assertion needs a
  marker on `window` (`markPageInstance` / `expectSamePageInstance` in the spec).

## Panel contract on this page

- The registered panel is `locations-filters`; its toggle is
  `#locations-filters-toggle` and the count badge is
  `[data-nav-panel-count]` inside it (server-rendered, `hidden` at zero).
- **Applying filters does not close the navbar panel.** Open it by reading
  `aria-expanded` rather than clicking the toggle again, or the second click
  closes it and `Clear Filters` becomes invisible (this cost one debugging round
  trip).
- Apply and Clear stay on `/locations` and never reload — the form is
  client-side here, so `page.url()` and the window marker both have to be
  asserted.

## Two regressions found and fixed (each named by a scenario)

1. `locations_map_follows_narrow_container_resizes` — the map was never told its
   new size on a rotation or a breakpoint change. Only `resize` and a
   `ResizeObserver` were wired.
2. `locations_unsaved_assignment_survives_a_resize_through_the_breakpoint` — the
   panel is rebuilt from `innerHTML` on every map move, filter change and layout
   transition, which silently discarded whatever the user had typed into
   `#mass-location-input`. The draft now lives in a closure
   (`pendingLocationName`) and is restored after each render.
   - Subtlety worth keeping: the draft is dropped only when `selectedPhotoIds`
     empties, **not** when `selectedClusters` comes back empty. Right after a
     resize the cluster source can briefly report no cluster for a selection that
     is still there, and clearing the draft in that window made the test flake.

## Test-data notes for the responsive cases

- `clickFirstCluster` sorts clusters by size, so at the initial fitted zoom it
  returns the multi-photo White House cluster — which is what the thumbnail
  assertions need (`.preview-thumb` only renders for two or more photos).
- A `.btn-quick-assign` chip only appears when selected photos already carry a
  location name, so the long-name and coarse-pointer tests assign one through
  `bulkUpdate` first and clear it again at the end.
- A thumbnail's `title` is the **filename** (`photo.name`), and `#photo-name`
  shows the same value — that pairing is what makes "tap the thumb" the
  coarse-pointer equivalent of the hover tooltip.
- `locations_configure_filter_sidebar` (pre-existing) flaked once across runs on
  `#filter-config-reset` not being visible on the modal's second open. It passed
  on re-run and is unrelated to the responsive work; worth watching.
