# People Test Triage Analysis

## Failure
`people_list_displays_all_people` fails at line 26: `createPersonViaApi` expects HTTP 201 but receives 403.

## Root Cause
The test uses Playwright's `APIRequestContext` (`request`) to call `POST /api/people/create`. The application has CSRF protection enabled in `security.py` via `@app.before_request protect_unsafe_request`. This blocks all non-safe HTTP methods unless a valid `X-CSRF-Token` header or `csrf_token` form field is present.

The browser's `security.js` intercepts `window.fetch()` and automatically injects `X-CSRF-Token` from `window.APP_CONFIG.csrfToken`. However, Playwright's `APIRequestContext` bypasses the browser entirely and doesn't include any CSRF token.

The face_assignment test suite (`face-assignment.spec.ts`) correctly handles this by using `page.evaluate()` with `fetch()` for API calls, which goes through `security.js`'s interceptor.

## Affected Functions
- `createPersonViaApi(request, name)` - uses `request.post('/api/people/create', ...)` → 403
- `assignFaceToPersonViaApi(request, faceId, personId)` - uses `request.post('/api/faces/assign', ...)` → 403  
- All inline cleanup calls `request.post('/people/${personId}/delete')` → silently fail with 403

## Fix Applied (Round 2)
### Round 1: API CSRF fix
Rewrote API helper functions to use `page.evaluate()` with `fetch()` (as the face_assignment suite does):
1. `createPersonViaApi` now accepts `Page` instead of `APIRequestContext`, navigates to `/people` first to load `APP_CONFIG.csrfToken`, then uses `page.evaluate()` with `fetch()`
2. `assignFaceToPersonViaApi` now accepts `Page` instead of `APIRequestContext`, uses `page.evaluate()` with `fetch()`
3. All inline cleanup `request.post(...)` calls replaced with `page.evaluate()`-based `fetch()` calls
4. Removed `APIRequestContext` import and `request` fixture from test signatures

### Round 3: Flash message consumed by fetch redirect
The `deletePersonViaApi` helper uses `fetch()` which follows redirects silently. When `fetch()` POSTs to `/people/<id>/delete`, the server sets a flash message and redirects to `/people` — but `fetch()` consumes the redirect response including the flash. By the time Playwright navigates to `/people`, the flash is gone. Fix: for the `people_can_delete_person` test body, use `page.evaluate()` to create and submit a form with CSRF token (via `window.APP_CONFIG.csrfToken`), which navigates the page naturally and renders the flash. The `deletePersonViaApi` helper (using fetch) is retained for cleanup calls where flash messages don't matter.
