# Face Assignment Test Failure Investigation

## Test: "should be able to assign faces to people"

### Failure Details
- **Status**: timedOut (30000ms timeout)
- **Line**: 115 - `await page.locator('#sidebar-person-select').selectOption({ label: 'Obama' });`
- **Error**: `element is not visible` (repeated many times before timeout)
- **Final action**: Element was detached from DOM, navigated to /people page

### Key Findings

1. **Application uses custom searchable-select component**
   - The `#sidebar-person-select` is a native `<select>` element
   - It has `display: none` set via CSS
   - A custom button UI is shown instead: `.searchable-select-display`
   - The component is defined in `/static/searchable-select.js`

2. **Test code issue**
   - Test attempts: `await page.locator('#sidebar-person-select').selectOption({ label: 'Obama' });`
   - This tries to interact with a hidden native select element
   - Playwright's `selectOption()` requires the element to be visible
   - The element is intentionally hidden because a custom dropdown UI replaces it

3. **Actual working interaction (verified manually)**
   - Click the button: `.searchable-select-display` 
   - Opens dropdown: `.searchable-select-dropdown`
   - Click option: `.searchable-select-option` containing "Obama"
   - This properly updates the underlying select and triggers change event

4. **Why test fails consistently**
   - The test has never worked with this UI implementation
   - The custom dropdown component makes the native select inaccessible to Playwright's standard select methods
   - The timeout happens because Playwright waits for visibility that will never come

5. **Test history pattern**
   - Consistently fails at the same line across multiple runs
   - No intermittency - always fails
   - Other tests in the suite pass (5 out of 6)

### Conclusion
This is a **test code defect**. The test code was written assuming a standard HTML select element, but the application uses a custom searchable-select component that hides the native select and presents a custom UI. The test needs to be updated to interact with the custom dropdown UI instead of trying to use `.selectOption()` on the hidden native select element.
