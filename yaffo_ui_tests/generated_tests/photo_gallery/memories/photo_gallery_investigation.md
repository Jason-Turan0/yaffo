# Photo Gallery Test Failure Investigation

## Issue Summary
All 3 tests are failing in the beforeEach hook with timeout errors while waiting for `.gallery-grid` element.

## Key Findings

1. **Wrong Selector in Test Code**
   - Test is looking for: `.gallery-grid`
   - Actual class in application: `.photo-grid`
   - Evidence from index.html template (line 33): `<div class="photo-grid">`
   
2. **Application is Working Correctly**
   - Page loads successfully at http://127.0.0.1:5001/
   - 14 photos are displayed in `.photo-grid` container
   - All `.photo-card` elements are present
   - Year select (#year-select) exists and works
   - Page size select (#page-size) exists
   - Pagination controls work (First, Prev, Next, Last links)
   - Photo dates are displayed in `.photo-date` elements

3. **Test History Pattern**
   - First 2 runs (15:01, 15:03): Only 1 test failed (gallery_page_navigation_works)
   - Last 3 runs (15:32, 15:33, 15:48): ALL 3 tests failed in beforeEach hook
   - This suggests the selector was changed/broken between run 2 and run 3

4. **Other Selectors Verified**
   - `.photo-card` - EXISTS ✓
   - `.photo-date` - EXISTS ✓
   - `select#year-select` - EXISTS ✓
   - `select#page-size` - EXISTS ✓
   - Pagination links (First, Prev, Next, Last) - EXIST ✓

## Classification
**test_code_defect** - The test is using the wrong CSS selector (`.gallery-grid` instead of `.photo-grid`)

## Affected Tests
All 3 tests are affected because they all use the faulty beforeEach hook:
1. gallery_loads_with_valid_images
2. gallery_filter_year_works
3. gallery_page_navigation_works

## Required Fix
Replace `.gallery-grid` with `.photo-grid` in:
- Line 12: `await expect(page.locator('.gallery-grid')).toBeVisible();`
- Line 23: `const imageLocators = await page.locator('.gallery-grid .photo-card img').all();`

## Verification via Live Testing
I manually tested all the spec goals on the live application:

✓ Gallery loads with photos - 14 photos displayed in `.photo-grid`
✓ Year filter works - filtered to 2014, got 4 photos from that year
✓ Clear filter works - returned to 14 photos
✓ Page size select works - changed to 10 items per page
✓ Next page navigation works - navigated from page 1 to page 2 (4 photos on last page)
✓ First page navigation works - navigated back to page 1
✓ Last page navigation works - navigated to page 2

The application fully supports all the spec goals. The only issue is the incorrect CSS selector in the test code.

## Additional Finding on Last Page
On the last page (page 2 of 2):
- Next link has class "page-btn disabled" ✓
- Last link has class "page-btn disabled" ✓
- The test expects `.toHaveClass(/disabled/)` which should work with "page-btn disabled"

## Pagination Template Analysis
The pagination.html template (lines 56-59, 63-66) adds the "disabled" class to links:
- `class="page-btn {% if current_page >= total_pages %}disabled{% endif %}"`
- This confirms the disabled class is applied correctly on the last page

The test code is mostly correct except for the `.gallery-grid` vs `.photo-grid` issue.

## Summary
The root cause is a **test_code_defect**: the test uses `.gallery-grid` but the application uses `.photo-grid`.
