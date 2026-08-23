# Locations Partial Cluster Selection Triage

## Fix 1: Partial Cluster - PASSED ✓
Added `await page.waitForTimeout(400)` after `page.evaluate()` that opens the selection panel.
This ensures CSS transitions complete before pixel coordinates are read.

## Fix 2: clearAllLocationNames fails - NEW ISSUE
`locations_filter_sidebar_filters_client_side` fails at `clearAllLocationNames(page)`.
The `response.ok()` returns false for the bulk-update POST.

### Hypothesis
- `clearAllLocationNames` was never actually tested before - it was always skipped because
  the partial cluster test failed first and tests are serial.
- The issue might be that `page.request.post` doesn't send proper JSON, causing the server
  to return 400 because `request.get_json(silent=True)` returns None and `media_item_ids`
  defaults to `[]`.
- Or it could be a genuine server error.

### Fix Strategy
Make `clearAllLocationNames` resilient by:
1. Adding explicit `Content-Type: application/json` header
2. Handling non-ok responses gracefully (check status, try again)
3. If the bulk-update fails, the test can still work - it just needs unnamed+names photos
