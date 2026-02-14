# Investigation Notes - Photo Gallery Pagination Test Failure

## Issue Summary
Test: `gallery_page_navigation_works - Verify that the page navigation works`
Error: Test timeout of 5000ms exceeded
Specific failure: `expect(page.locator('.photo-card')).toHaveCount(10)` failed with actual count of 14

## Key Findings

### 1. URL Parameter Issue
- When page size is set to 10, URL updates correctly to `?page=1&page-size=10&person-match-type=any&location-match-type=any`
- However, the application is NOT respecting the `page-size` parameter from the URL
- All 14 photos are displayed instead of 10

### 2. Backend Route Analysis (home.py)
Line 32: `page_size = request.args.get("PAGE_SIZE", type=int)`
**Problem**: The route is looking for "PAGE_SIZE" (uppercase) in query params, but the URL contains "page-size" (lowercase with hyphen)

Line 33: `filter_page_size = page_size if page_size else 25`
This defaults to 25 when page_size is None

### 3. Application Behavior
- The pagination component correctly generates URLs with `page-size` parameter
- The backend is NOT reading this parameter (case mismatch)
- Result: Backend always uses default page size of 25
- Since there are only 14 photos total, all 14 are displayed regardless of the page-size parameter

### 4. Test History Pattern
- Passed on: 2026-02-14T14:22:43.961Z and 2026-02-14T14:58:54.152Z
- Failed on: 2026-02-09 (2 times) and 2026-02-14T15:01:35.008Z
- This suggests an intermittent issue OR a recent regression in the application

## Root Cause
**Application Regression**: The backend route has a parameter name mismatch. It expects "PAGE_SIZE" but the pagination component sends "page-size".

This is NOT a test code defect - the test is using standard Playwright selectors (`selectOption({ label: '10' })`) which triggers the page size dropdown correctly.

This is NOT environment instability - the failure is consistent and reproducible based on the parameter mismatch.

## Verification
Tested with URL using uppercase: `/?page=1&PAGE_SIZE=10`
Result: Shows exactly 10 photos and pagination shows "Page 1 of 2"
This confirms the backend expects "PAGE_SIZE" but the UI sends "page-size"

## Classification
**application_regression**: The application has a bug where the page-size query parameter is not being read correctly due to a case/format mismatch between frontend and backend.
- Backend expects: `PAGE_SIZE` (uppercase, underscore)
- Frontend sends: `page-size` (lowercase, hyphen)
- Result: Pagination doesn't work when using the UI controls
