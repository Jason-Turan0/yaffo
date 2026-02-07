# SOLUTION READY

Fix the "face page navigation works" test by reordering the steps:
1. Apply the group-by-people filter FIRST
2. THEN change the page size (which will navigate and preserve filter)

This prevents the page-size selector's immediate navigation from losing the filter state.
---
# Face Assignment Test Debugging

## Failed Test: face page navigation works

### Error Details
- **Error**: Element `.grid` not found after applying filters
- **Location**: Line 366
- **Timeout**: 5000ms exceeded
- **Status**: Element not visible

### Investigation

#### Template Analysis (faces/index.html)
1. **Grid containers**: `.grid` elements ARE present in the template
2. **Grid location**: Inside `.suggestion-group` divs
3. **Key finding**: Grids only appear when `face_suggestions | length > 0`
4. **Empty state**: If no results, shows "No results found" message

#### Pagination Component (components/pagination.html)
- Uses page-size selector with onchange handler
- Navigation buttons: First, Prev, Next, Last
- Page info showing "Showing X-Y of Z results"

### Root Cause
The test checks `#group-by-people` radio button and then applies filters. However:
1. When grouping by people WITHOUT setting a similarity threshold properly
2. Or when there are no unassigned faces matching the criteria
3. The `face_suggestions` array might be EMPTY
4. This means NO `.grid` elements are rendered
5. The test expects `.grid` to be visible but it doesn't exist in the DOM

### Solution Options

#### Option 1: Wait for content and handle empty state
- Check if `.suggestion-group` exists after applying filters
- If no groups, skip pagination tests
- This is fragile because it depends on data

#### Option 2: Change selector strategy
- Instead of checking `.grid`, check for `.suggestion-group` or `.main-content`
- The main-content is always present
- Only check grid if suggestion-groups exist

#### Option 3: Fix the page-size selection issue
Looking at line 358:
```typescript
await page.selectOption('select#page-size', '25');
```

But in the pagination component template:
```html
<select id="page-size" name="page-size" onchange="window.location.href = this.value">
```

The onchange handler navigates immediately! This means:
1. selectOption will trigger navigation
2. The subsequent "Apply Filters" click is unnecessary
3. Need to wait for navigation after selectOption

#### BEST SOLUTION: Fix the flow
1. Don't use selectOption - it triggers immediate navigation
2. Instead, navigate directly with page-size in URL
3. Or wait after selectOption for the navigation to complete
4. The test currently does both selectOption AND Apply Filters - this is wrong

### FINAL ANALYSIS

The problem is:
1. Line 358: `await page.selectOption('select#page-size', '25');`
   - This triggers onchange which navigates to a NEW URL
   - But we haven't applied the group-by-people filter yet!
   
2. Line 361: `await page.click('button.btn.btn-primary.filter-btn');`
   - This applies the filter
   
The correct flow should be:
1. Navigate to /faces
2. Check group-by-people
3. Apply filters FIRST (this will use default page size)
4. THEN change page size (which will navigate with filters preserved)

OR alternatively:
1. Navigate to /faces with all query params including page-size and group_by
2. Skip the filter application

The key issue: The test is trying to set page size BEFORE applying filters, but the page size selector navigates immediately, so the group-by-people checkbox state is lost.

### CORRECTED TEST APPROACH

**Current broken flow:**
```typescript
await page.check('#group-by-people');           // Check the radio
await page.selectOption('select#page-size', '25');  // This navigates! Loses radio state
await page.click('button.btn.btn-primary.filter-btn');  // Apply filter button
```

**Fixed flow option 1 - Apply filters first:**
```typescript
await page.check('#group-by-people');
await page.click('button.btn.btn-primary.filter-btn');  // Apply filters
await page.waitForLoadState('networkidle');
// Now page-size change will preserve the group_by param
await page.selectOption('select#page-size', '25');
await page.waitForLoadState('networkidle');
```

**Fixed flow option 2 - Direct navigation:**
```typescript
await page.goto('/faces?group_by=people&page-size=25&page=1');
await page.waitForLoadState('networkidle');
// Skip filter application entirely
```

I'll use Option 1 as it's more realistic user flow.

## ADDITIONAL FIX: Navigation button selectors

The current test uses:
```typescript
const nextButton = page.locator('a:has-text("Next"):not([disabled])');
```

But looking at the pagination template, disabled links have class `.disabled`, not attribute `[disabled]`:
```html
<a href="..." class="page-btn {% if current_page >= total_pages %}disabled{% endif %}"
```

So the selector should be:
```typescript
const nextButton = page.locator('a:has-text("Next"):not(.disabled)');
```

Also need to check if the button exists before clicking.

Looking at template more closely:
- Button text is "Next &rsaquo;" not just "Next"
- Need to use text matching that accounts for HTML entities

Better selectors:
```typescript
const nextButton = page.locator('.page-btn:not(.disabled):has-text("Next")');
const firstButton = page.locator('.page-btn:not(.disabled):has-text("First")');
const lastButton = page.locator('.page-btn:not(.disabled):has-text("Last")');
```
