# Locations Test Triage Notes

## Key Findings

1. **Test result: 0/0/0/0** - No tests discovered by the test runner
2. **File modified**: Aug 01 2026 17:30:03 (test run at 17:31:43 - just 100 seconds later)
3. **Previous runs**: Successful on Jul 2 (5 tests, then 6 tests), verified 11 tests on Jul 5
4. **Pattern match**: Other triaged test suites (albums, face_assignment, people) all used `page.request.post()` which bypasses CSRF

## Potential Issues

### A. `Page` import from @playwright/test
```typescript
import { test, expect, Page } from '@playwright/test';
```
`Page` is a type-only export in newer Playwright versions. With `verbatimModuleSyntax: true`, this would fail compilation. Other test files use the same pattern, BUT the locations file was regenerated and may have been generated differently or tested against a stricter config.

### B. CSRF bypass via page.request.post()
Multiple functions use `page.request.post()` which bypasses CSRF:
- `clearAllLocationNames()` - line ~222
- `assignAllLocationNames()` - line ~231
- `locations_filter_sidebar_filters_client_side` test - inline `page.request.post()`
- `locations_recommends_existing_nearby_name` test - `page.request.post()`
- `locations_select_clusters_and_assign_name` test - `page.request.get()` for detail verification

The same pattern was flagged in albums, face_assignment, and people triage analyses.

### C. `test.describe.configure` at module top-level
This is used in other working spec files too, so probably not the issue.

## Seed Data
- Chicago photos: `2015_chicago_baby_trip/` - 4 photos with GPS
- Beach photos: `2021_gulf_beach_trip/` - 13 photos with GPS
- Other photos in other directories may or may not have GPS
- Test image references match seed data: NAMED_CHICAGO_IMAGE, SELECTED_CHICAGO_IMAGE, DISTANT_IMAGE all exist

## Application
- `/locations` route serves template with OpenLayers map
- `/locations/bulk-update` POST endpoint for mass location name updates
- `/locations/reverse-geocode` POST endpoint for reverse geocoding
- CSRF protection via `security.py` - blocks non-safe methods without CSRF token
- `page.request.post()` bypasses browser, has no CSRF token → 403

## Fix Plan
1. Change `import { test, expect, Page }` → `import { test, expect }` + `import type { Page }`
2. Rewrite `clearAllLocationNames`: `page.request.post` → `page.evaluate` + `fetch`
3. Rewrite `assignAllLocationNames`: `page.request.post` → `page.evaluate` + `fetch`
4. Fix inline `page.request.post` in `locations_filter_sidebar_filters_client_side`
5. Fix `page.request.post` in `locations_recommends_existing_nearby_name`
6. Fix `page.request.get` calls in `locations_select_clusters_and_assign_name` and `locations_clear_location_names`
