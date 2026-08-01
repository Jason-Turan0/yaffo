# Triage: locations_partial_cluster_selection_indicator failure

## Error
Line 336: `await expect.poll(() => selectedIds(page)).toEqual(partial!.ids);`
Expected: [1,10,11,12,13,14,2,28,29,3,30,31,4,5,6,7,8,9]
Received: [] (empty array)

## Root Cause Analysis

The click handler code in list.js is correct:
```javascript
map.on('click', function(evt) {
    const feature = map.forEachFeatureAtPixel(evt.pixel, callback);
    if (feature) {
        selectedPhotoIds.clear();
        if (!selection.selected) {
            selection.photoIds.forEach(id => selectedPhotoIds.add(id));
        }
    } else if (!isShiftClick && selectedPhotoIds.size > 0) {
        selectedPhotoIds.clear();  // ← THIS branch runs!
    }
});
```

The `else if` branch clears the selection because `map.forEachFeatureAtPixel` returns null — the click misses the cluster feature.

## Why the click misses

1. The test programmatically sets up a partial selection by calling `updateSelectionPanel()` + `renderSync()` inside `page.evaluate`.
2. `updateSelectionPanel()` adds the `active` class to `#selection-panel`, which starts a 0.3s CSS transition on the panel width (0 → 380px) and margin-left.
3. Because `.map-layout` is `display: flex` with `#map { flex: 1 }`, the map resizes during the panel transition.
4. `renderSync()` is called *during* the transition, rendering the map at an intermediate size.
5. After the transition completes (~0.3s later), the map element is at the final size, but OpenLayers' internal render frame was created at the intermediate size.
6. When `getPixelFromCoordinate` runs in `clusterSummaries`, it returns pixels based on the current viewport, but `forEachFeatureAtPixel` uses the last render frame which is at a different scale.
7. The pixel mismatch causes the click to land on a spot where no feature is rendered → `forEachFeatureAtPixel` returns null → selection is cleared.

## Classification: test_code_defect

The application code correctly implements the spec behavior (clicking a partial cluster selects all its photos). The test fails because it doesn't wait for the CSS panel transition to complete before computing pixel coordinates and clicking. The map needs to re-render at the final size for hit detection to work correctly.
