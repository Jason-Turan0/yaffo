# Triage: locations_partial_cluster_selection_indicator failure

## Error
Line 336: `await expect.poll(() => selectedIds(page)).toEqual(partial!.ids);`
- Expected: full cluster IDs (e.g., [1,10,11,...,9])
- Received: [] (empty)
- Poll timed out after 5s, always returning empty array

## Root Cause Analysis

### Test flow:
1. `openMap(page)` → map loaded with clusters
2. `clusterSummaries(page)` → find multi-photo cluster
3. `page.evaluate(...)` → programmatically set partial selection:
   - `selectedPhotoIds.clear()`
   - Add all-but-one of cluster's IDs to selectedPhotoIds
   - Call `updateSelectionPanel()` → panel gets `active` class
   - Call `renderSync()`
4. `clusterSummaries(page)` → find partial cluster, compute pixel positions
5. `clickCluster(page, partial)` → click at computed pixel
6. `expect.poll(() => selectedIds(page)).toEqual(partial!.ids)` → FAILS

### CSS Transition Issue:
- `.selection-panel.active` has CSS transition: `width 0.3s ease, opacity 0.3s ease, ...`
- When `updateSelectionPanel()` adds `active` class, panel expands from 0→380px over 0.3s
- Map (`flex: 1`) shrinks accordingly
- `renderSync()` is called immediately, but CSS transition hasn't started/completed
- OpenLayers may not have detected the resize
- `clusterSummaries` computes `api.map.getPixelFromCoordinate()` which may use stale internal size
- Click at that pixel may land on empty space → click handler clears selection (empty map branch)

### Click handler logic (correct behavior):
For a non-shift click on a partial cluster:
- `selection.selected` = false (partial)
- `selectedPhotoIds.clear()` then `selectedPhotoIds.add(all_ids)` → should become full
- But only if `map.forEachFeatureAtPixel(evt.pixel)` actually finds the cluster

### Why it becomes empty:
If click misses cluster (due to wrong pixel from stale map size), the "empty map" branch runs:
```
} else if (!isShiftClick && selectedPhotoIds.size > 0) {
    selectedPhotoIds.clear();  // clears partial selection
    ...
}
```

## Classification: test_code_defect

The test doesn't wait for the CSS transition (panel appearing) to complete and for OpenLayers to detect the map resize before computing pixel coordinates for the click.

## Suggested Fix
After the evaluate that sets up partial selection and calls updateSelectionPanel(), add a wait for the CSS transition and map stability:
```typescript
await page.waitForTimeout(500);  // Let CSS transition complete
await waitForMapRender(page);    // Ensure OpenLayers is stable at new size
```

Alternatively, avoid calling updateSelectionPanel() in the setup evaluate (only set selectedPhotoIds + renderSync), since the cluster marker styling reads from selectedPhotoIds directly and doesn't need the panel to be rendered.
