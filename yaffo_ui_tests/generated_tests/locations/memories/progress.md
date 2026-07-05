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
