# Investigation: themes_set_default_theme failure

## Root Cause: CSRF token missing from page.request.post calls

### How CSRF works in this app:
1. `security.py` registers `protect_unsafe_request` as a `@app.before_request` handler
2. It checks `csrf_is_valid()` which reads `X-CSRF-Token` header or `csrf_token` form field
3. `security.js` configures htmx to auto-add `X-CSRF-Token` header to all htmx requests
4. `page.request.post` does NOT go through htmx and does NOT include the CSRF token

### What happens in the failing test:
1. `try` block: "Make default" click uses htmx → CSRF token sent → works fine
2. `finally` block: `page.request.post('/themes/classic/default')` → no CSRF token → 403 → default NOT restored
3. `page.goto('/people')` → server still has custom theme as default → `data-theme` is custom slug
4. Test fails at line 229

### Why other tests pass despite same issue:
- `deleteThemeViaApi` has `.catch(() => {})` which masks failures
- Other tests don't verify the result of API-level POSTs
- Tests use unique names so leftover themes don't conflict

### Evidence:
- `security.js` shows htmx gets CSRF token but page.request.post doesn't
- `protect_unsafe_request` checks CSRF for all non-safe methods
- No `DEMO_MODE` (otherwise all theme write ops including htmx ones would fail)
- Error shows expected "classic" but received custom theme slug, proving restore didn't happen
