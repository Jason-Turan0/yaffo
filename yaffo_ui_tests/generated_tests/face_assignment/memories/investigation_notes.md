# Face Assignment Investigation

## Failing Test
- **Test**: "should be able to assign faces to people"
- **Error**: Line 54: `expect(response.ok()).toBeTruthy()` - Received false
- The API call `POST /api/people/create` with `{name: 'Obama'}` returns non-OK, non-400 response

## Root Cause Analysis

### CSRF Protection Mechanism
The application has CSRF protection enabled via `security.py`. The `@app.before_request` handler checks ALL non-safe (POST/PUT/DELETE) requests:
- Bypassed when `TESTING=True` or `DEMO_MODE=True` or `CSRF_ENABLED=False`
- The browser's `fetch()` is monkey-patched by `security.js` to automatically add `X-CSRF-Token` header
- BUT: regular HTML form submissions (like the delete form in `people/list.js`) do NOT include CSRF tokens
- The `request` fixture in Playwright (APIRequestContext) does NOT go through the browser's fetch → no CSRF token

### Test Flow Analysis
1. **Test 1** (passes): Creates Obama via UI → `fetch('/api/people/create')` includes CSRF token via security.js → works
2. **afterEach**: Navigates to /people, clicks delete link → JS creates form WITHOUT CSRF token → form POST to `/people/<id>/delete` → CSRF check fails → returns 403 page → `expect(personRow).toHaveCount(0)` passes (403 page has no table) → **Obama NOT actually deleted**
3. **Test 2**: `createPersonViaApi` calls `request.post('/api/people/create')` WITHOUT CSRF token → CSRF check fails → returns 403 JSON → `response.status() === 400` is false → falls through to `expect(response.ok())` → **FAILS**

### Why people.spec.ts Works
The people.spec.ts tests use unique timestamp-based names and never depend on prior deletion. Their `request.post` calls also lack CSRF tokens but might be running in a different test worker with different configuration.

### Test Run History
- Jul 1: All 6 passed (possibly CSRF was not yet enforced, or TESTING=True was set)
- Jul 19+: Consistent failure at the same line (CSRF enforcement was added or TESTING config changed)

## Classification: **test_code_defect**

The `createPersonViaApi` helper uses `request.post()` which bypasses the browser's CSRF interceptor. The test should use the UI to create the person (like test 1 does) or use `page.evaluate()` to call fetch through the browser.

## Fix Attempt 1 Results
- `createPersonViaApi` with `page.evaluate(fetch)` — works for API calls (goes through security.js)
- `deletePersonByName` with `page.evaluate(form.submit)` — FAILED; `form.submit()` inside evaluate doesn't reliably trigger Playwright-detectable navigation
- Need to use `fetch` for delete too, then manually navigate to /people afterward

## Fix Attempt 2 Plan
- `deletePersonByName`: use `page.evaluate(fetch)` with CSRF token header for the POST, then `page.goto('/people')` to refresh
- `createPersonViaApi`: keep the `page.evaluate(fetch)` approach from fix 1
- `assignFaceToPersonViaApi`: keep the `page.evaluate(fetch)` approach from fix 1
