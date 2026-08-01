# Triage Analysis: settings_remove_media_directory

## Failure
- Test: `Settings › settings_remove_media_directory`
- Error: `expect(response.ok()).toBeTruthy()` → Received: `false`
- The `request.post('/api/settings/media-dirs', { data: { directory: scratchDir } })` returns a non-OK response (403 CSRF failure).

## Root Cause (UPDATED)
CSRF protection! The Flask app has CSRF enabled via `security.py`. The `@app.before_request protect_unsafe_request` blocks all POST/PUT/DELETE without a valid CSRF token. `static/security.js` intercepts all `fetch()` calls and automatically injects `X-CSRF-Token` from `window.APP_CONFIG.csrfToken` — that's why the page UI test (test 1) passes. But the Playwright `request` fixture is an isolated APIRequestContext with no session cookie and no CSRF token header, so the Flask CSRF check fails and returns 403.

## Evidence
- `security.py`: `csrf_is_valid()` checks `X-CSRF-Token` header or `csrf_token` form field against session-stored token
- `security.py`: `@app.before_request protect_unsafe_request` blocks unsafe methods unless CSRF is valid, TESTING mode, or DEMO_MODE
- `static/security.js`: intercepts `window.fetch` and adds `X-CSRF-Token` from `window.APP_CONFIG.csrfToken`
- `settings.spec.ts` test 1 passes via page UI (browser fetch → intercepted → CSRF token added)
- `settings.spec.ts` test 2 fails via `request.post()` (isolated context → no cookies → no CSRF token)

## Fix
Use `page.request.post()` (shares browser context cookies) AND read `window.APP_CONFIG.csrfToken` from the page to set the `X-CSRF-Token` header.
