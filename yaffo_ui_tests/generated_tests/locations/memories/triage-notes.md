# Triage: locations_partial_cluster_selection_indicator failure

## Error
Line 336: `await expect.poll(() => selectedIds(page)).toEqual(partial!.ids);`
- Expected: 18 photo IDs (partial!.ids)
- Received: [] (empty array)
- Timeout 5000ms exceeded

## Key Code Analysis

### Click Handler (list.js lines ~388-408)
```javascript
map.on('click', function(evt) {
    const feature = map.forEachFeatureAtPixel(evt.pixel, ...);
    if (feature) {
        const selection = getClusterSelection(feature);
        if (!isShiftClick) {
            selectedPhotoIds.clear();
            if (!selection.selected) {
                selection.photoIds.forEach(id => selectedPhotoIds.add(id));
            }
        }
        clusterLayer.changed();
        updateSelectionPanel();
    } else if (!isShiftClick && selectedPhotoIds.size > 0) {
        selectedPhotoIds.clear();
        clusterLayer.changed();
        updateSelectionPanel();
    }
});
```

### Test Flow
1. openMap - map rendered
2. clusterSummaries - captures initial clusters
3. Find multiCluster (total > 1)
4. page.evaluate: set partial selection, call updateSelectionPanel(), renderSync()
5. clusterSummaries - captures clusters again, finds "partial" cluster
6. Assertions on partial cluster PASS (selected count, iconSrc, panel text)
7. clickCluster(page, partial!) - clicks at cluster pixel coordinates
8. expect.poll(selectedIds) - FAILS with []

### getClusterSelection
- `selected` = selectedCount === totalCount
- For partial: selectedCount=17, totalCount=18 → selected=false
- Handler should: clear → add all 18 IDs

### CSS Layout
- `.map-layout` uses `display: flex`
- `#map` has `flex: 1`
- `.selection-panel.active` has `width: 380px; margin-left: 15px`
- Panel has `transition: width 0.3s ease, ...`
- When panel opens, map shrinks by 395px

### Root Cause Confirmed: CSS Transition Timing

The `.selection-panel` has `transition: width 0.3s ease, opacity 0.3s ease, padding 0.3s ease, margin-left 0.3s ease`. The `.map-layout` uses `display: flex` with `#map` at `flex: 1`.

When `page.evaluate` calls `updateSelectionPanel()`, the panel gets `classList.add('active')`, which changes its width from 0 to 380px + 15px margin. The map (flex: 1) shrinks by 395px, but this resize is ANIMATED over 0.3s via CSS transition.

The test flow:
1. `page.evaluate` opens panel → CSS transition starts (0.3s)
2. `clusterSummaries` runs `page.evaluate` → captures `pixel = getPixelFromCoordinate(coord)` at time T1 (map may be mid-transition)
3. Several assertions run (taking time)
4. `clickCluster` → `screenPointForCluster` → `boundingBox()` at time T2 (transition likely completed)
5. Click coordinate = `boundingBox().x + pixel[0]` where pixel[0] is from T1

If the map width at T1 ≠ map width at T2, the pixel coordinate from `getPixelFromCoordinate` is computed for a different viewport size than the one used for the bounding box. The click lands at the wrong position, misses the cluster feature entirely, and the else-if branch in the click handler clears the selection.

### Why Only This Test Fails
- Other cluster-click tests click BEFORE the panel opens (first cluster click triggers panel open)
- This test opens the panel PROGRAMMATICALLY via `updateSelectionPanel()`, then tries to click
- The CSS transition creates a timing window where pixel coordinates become stale

### Evidence
- Test was passing on 2026-07-05 (full 11-test suite verified)
- Fails consistently on 2026-08-01
- The click handler code logic is correct for partial→full→unselected cycle
- Other cluster-click tests pass because they click before panel CSS transition
- The error shows empty selection (click missed feature, else-if cleared)
