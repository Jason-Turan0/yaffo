# Photo Gallery Test Investigation

## Test Failure Analysis

### Failing Test
- Test: `gallery_page_navigation_works`
- Error: Expected `.photo-card` count to be 10, but found 14
- Status: Timeout (5000ms exceeded)

### Root Cause Investigation

#### 1. Page Size Selection Behavior
The test attempts to select page size "10" from the dropdown, but the implementation has a critical issue:

**In pagination.html:**
```html
<select id="page-size" name="page-size" onchange="window.location.href = this.value">
    {% for size in page_sizes %}
    <option value="{{ base_url }}?page=1&page-size={{ size }}{{ extra_query }}"
            {% if size == page_size %}selected{% endif %}>
        {{ size }}
    </option>
    {% endfor %}
</select>
```

The `<select>` has an `onchange` handler that navigates to the URL stored in the option's value attribute. When Playwright's `selectOption()` is called, it triggers this navigation automatically.

**However, the test code does:**
```typescript
await pageSizeSelect.selectOption({ label: '10' });
await page.waitForURL('**/?page=1&page-size=10**');
```

The problem is that after `selectOption()` is called:
1. The onchange handler fires and navigates the page
2. The URL changes to include `page-size=10`
3. BUT the page still displays 14 photos instead of 10

#### 2. Backend Query Parameter Issue
In `routes/home.py`, line 47:
```python
page_size = request.args.get("PAGE_SIZE", type=int)
```

**The parameter name is "PAGE_SIZE" (uppercase), but the URL uses "page-size" (lowercase with dash).**

This is a clear mismatch. The backend is looking for `PAGE_SIZE` but receives `page-size`, so it defaults to 25 photos.

#### 3. Verification
- Current URL: `http://127.0.0.1:5001/?page=1&page-size=10&person-match-type=any&location-match-type=any`
- Photo count on page: 14 photos (all photos in database)
- Expected: 10 photos

The page displays ALL 14 photos because:
- The backend doesn't recognize the `page-size` parameter
- It defaults to `filter_page_size = 25`
- Since there are only 14 total photos, all are displayed

## Classification: APPLICATION_REGRESSION

This is an application bug, not a test code defect:
- The backend expects `PAGE_SIZE` (uppercase)
- The frontend template generates `page-size` (lowercase with dash)
- The parameter name mismatch causes pagination to fail
- The test is correctly written and would pass if the application worked properly

## Suggested Fix
The application needs to be fixed to use consistent parameter naming. Either:
1. Change backend to use `page-size`: `request.args.get("page-size", type=int)`
2. OR change frontend template to use `PAGE_SIZE` in the query string

The test code is correct and does not need changes.
