# Triage: themes_set_default_theme failure

## Classification: test_code_defect

## Root Cause
The test uses `page.request.post()` (Playwright APIRequestContext) to call `/themes/<slug>/default` to restore the original default theme. The Flask app has CSRF protection enabled via `security.py`. The `before_request` hook blocks POST requests without a valid `X-CSRF-Token` header.

Browser `fetch` and htmx requests include the CSRF token automatically via `security.js`, but `page.request.post()` is a raw API-level request that bypasses the browser — no CSRF token is sent.

Result: POST gets 403, test doesn't check response, default is never restored, final assertion fails.

## Evidence
1. `security.py`: CSRF_ENABLED=True by default. Bypassed only for GET/HEAD/OPTIONS, TESTING=True, or DEMO_MODE=True.
2. `security.js`: Injects X-CSRF-Token into browser fetch/htmx. `page.request.post()` bypasses all JS.
3. UI clicks work (htmx includes token), API posts silently fail.
4. `deleteThemeViaApi` has same silent-failure pattern.
5. Other tests pass because they don't verify the API call effects.

## Affected Tests
- `themes_set_default_theme` (line 212) - fails
- All tests using `deleteThemeViaApi` - silently fail to clean up but don't verify

## Fix Strategy
Include CSRF token in `page.request.post()` calls:
1. Extract token: `const csrfToken = await page.evaluate(() => window.APP_CONFIG.csrfToken);`
2. Pass as header: `headers: { 'X-CSRF-Token': csrfToken }`
3. Apply to both the default-restore POST and `deleteThemeViaApi`
4. For `afterAll`, navigate to a page first to get the token
