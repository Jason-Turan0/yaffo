# Locations Tests — Current State (2026-07-01)

## Status: PASSING (6/6) against the isolated sandbox (verified twice; the suite restores seeded state)

## Application facts (verified against live app)
- `/locations` renders a canvas OpenLayers map into `#map`. Markers/clusters are drawn on canvas — they are NOT DOM elements; never try to locate them with selectors.
- `initMap` exposes its API at `window.PHOTO_ORGANIZER.locations.map`: `{map, vectorSource, selectedFeatures, updateSelectionPanel, applyFilter}`. Use it for feature counts and for `map.getPixelFromCoordinate(...)` → add the `#map` bounding box origin → real-mouse click targets.
- Wait for the global to exist AND a `rendercomplete` event before pixel math; the view is `fit()` to the data (zoom > 2) when any features exist.
- Located photos are embedded server-side (`locations | tojson` into `initMap`). Seeded sandbox: only photo 14 (DSCN0010.jpg, lat 43.4674 / lon 11.8851) has GPS; its `location_name` starts null.

## Interactions
- Marker click → `#popup` (an OL Overlay: present in DOM but hidden until positioned). Content: `img.popup-photo` (photos: `/media/{id}`, videos: poster; `data-fallback` = placeholder), `<h3>` filename, link to `/media/view/{id}`. GOTCHA: the popup's first `<a>` is `#popup-closer` (`href="#"`) — scope link assertions to `#popup-content`. Multi-photo clusters render a searchable-select to switch photos.
- `#filter-unnamed` checkbox re-filters `vectorSource` in place (unnamed = `!feature.get('name')`) and re-fits the view.
- Shift+drag (OL DragBox with `shiftKeyOnly`) selects clusters → `#selection-panel` gains `.active` with `.mass-assignment-info`, quick-assign buttons for existing names, `#mass-location-input` + `#mass-assign-btn`, and a clear-selection button.
- Assign POSTs `/locations/bulk-update` `{media_item_ids, location_name}` → `.notification.visible` toast, selection clears, feature `name`s update in place, server persists (photo's `/media/view/{id}` page shows the name).
- On selection the panel also auto-POSTs the first cluster's centroid to `/locations/reverse-geocode` (external OSM Nominatim) and inserts a `.btn-recommended` quick-assign button on success. ALWAYS `page.route()`-mock this endpoint — offline-safe and deterministic.

## Clearing location names (added 2026-07-01)
- `POST /locations/bulk-update` with `{media_item_ids, clear: true}` removes location names. `clear` must be boolean `true`; an empty `location_name` alone is still a 400 (`location_fields_required`) so a blank input can never wipe names by accident. Response carries `location_name: null`.
- The selection panel has a `.btn-clear-names` button ("Clear location names") wired to it; success toast key `locations:update.cleared` (all 7 locale catalogs carry the keys). Backend covered by `tests/yaffo/routes/test_locations.py`.
- Test ordering: the file runs `serial` with the two mutation tests LAST — assign "Test Beach", then clear — so the suite restores the seeded unnamed state and re-runs start pristine.
- The read-only tests still compute expectations from live `vectorSource` data (e.g. unnamed count captured before filtering) rather than assuming seed state.
