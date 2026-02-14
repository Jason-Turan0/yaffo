# Photo Gallery Investigation

## Issue Summary
All 3 tests in photo_gallery.spec.ts are timing out in the beforeEach hook at line 12:
```
await expect(page.locator('.gallery-grid')).toBeVisible();
```

Error: element(s) not found for `.gallery-grid`

## Key Findings

### 1. Selector Mismatch
- **Test expects**: `.gallery-grid` 
- **Application uses**: `.photo-grid`

### 2. Evidence from HTML Template
From `/Users/jason.turan/projects/yaffo/yaffo/templates/index.html`:
```html
<div class="photo-grid">
    {% for photo in photos %}
    <div class="photo-card" ...>
```

The container class is `photo-grid`, NOT `gallery-grid`.

### 3. Browser Verification
Live page inspection confirms:
- `.gallery-grid` does NOT exist
- `.photo-grid` EXISTS and contains 14 `.photo-card` elements
- All other selectors used in tests are correct:
  - `select#year-select` ✓
  - `select#page-size` ✓
  - `.photo-card` ✓

### 4. Test History Pattern
- 2026-02-14T14:22:43: ✓ All 3 tests passed
- 2026-02-14T14:58:54: ✓ All 3 tests passed
- 2026-02-14T15:01:35: ✗ 1 test failed (gallery_page_navigation_works)
- 2026-02-14T15:03:09: ✗ 1 test failed (gallery_page_navigation_works)
- 2026-02-14T15:32:15: ✗ ALL 3 tests failed (beforeEach timeout)

The tests were passing earlier today, then one test started failing, and now ALL tests fail in beforeEach. This suggests the application template was recently changed from `.gallery-grid` to `.photo-grid`.

## Root Cause
**APPLICATION REGRESSION**: The application's HTML template changed the CSS class from `.gallery-grid` to `.photo-grid`, breaking the test's selector in the beforeEach hook.

## Classification
This is an **application_regression** because:
1. Tests were previously passing (proven by test history)
2. The test code uses a selector that was correct at the time of writing
3. The application changed its DOM structure (class name)
4. The failure is consistent, not intermittent (all 3 tests fail the same way)
