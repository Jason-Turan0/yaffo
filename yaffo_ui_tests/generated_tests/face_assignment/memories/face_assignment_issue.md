# Face Assignment Test Failure Investigation

## Issue
Test "should be able to assign faces to people" is timing out at line 115 where it tries to select an option from the person dropdown.

## Root Cause
The test is using Playwright's native `selectOption()` on a `<select>` element that has been replaced with a custom searchable dropdown component. The original `<select>` has `style="display: none;"` applied to it, making it invisible to Playwright's visibility checks.

## Evidence from Browser
1. When clicking the dropdown button, a custom dropdown appears with:
   - A search input field
   - Custom dropdown options rendered as divs
   - The underlying select element is hidden

2. The searchable-select.js component:
   - Hides the original select element with `this.select.style.display = 'none';`
   - Creates a custom dropdown UI
   - Updates the underlying select when an option is clicked
   - Triggers a 'change' event on the select after updating

## Solution
Instead of using `page.locator('#sidebar-person-select').selectOption()`, the test needs to:
1. Click the dropdown button to open it
2. Click the desired option in the custom dropdown

The test can either:
- Click the button then click the option div
- Or use `force: true` on the hidden select (not recommended)
- Or directly trigger the change event via evaluate() after setting the value

Best approach: Interact with the visible custom dropdown UI as a user would.

## Verified Fix
Tested successfully in browser:
```javascript
await page.locator('.searchable-select-display').click();
await page.locator('.searchable-select-option').filter({ hasText: 'Obama' }).click();
```

This properly:
- Opens the dropdown
- Selects the option
- Updates the underlying select value
- Triggers the change event
- Updates the display text

## Code Change Required
Line 115 in the test file needs to be replaced:
```typescript
// OLD (fails):
await page.locator('#sidebar-person-select').selectOption({ label: 'Obama' });

// NEW (works):
await page.locator('.searchable-select-display').click();
await page.locator('.searchable-select-option').filter({ hasText: 'Obama' }).click();
```
