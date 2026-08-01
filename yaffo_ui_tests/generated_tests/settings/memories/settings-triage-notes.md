# Settings Test Failure Triage

## Failing Tests (Round 1)
- `Settings › settings_remove_media_directory` at line 76: `request.post()` lacks CSRF token

## Failing Tests (Round 2)
- `Settings › settings_change_language` at line 131: `page.request.post()` form data missing `csrf_token`

## Root Cause: CSRF Protection

The application's `security.py` enables CSRF protection on all non-GET/HEAD/OPTIONS requests.
The CSRF check requires either:
- `X-CSRF-Token` header (added by `security.js` when using browser `fetch()`)
- `csrf_token` form field (present in server-rendered `<form>` elements)
- Or `TESTING`/`DEMO_MODE` config (neither is set in this environment)

Three different POST mechanisms and their CSRF status:

| Mechanism | CSRF Works? | Why |
|-----------|-------------|-----|
| Form submit button click | ✅ Yes | Hidden `<input name="csrf_token">` in template form |
| `page.evaluate(() => fetch(...))` | ✅ Yes | `security.js` overrides `fetch()` to add `X-CSRF-Token` header |
| `request.post()` (APIRequestContext) | ❌ No | Standalone HTTP client, no browser session/cookies |
| `page.request.post()` (APIRequestContext from page) | ❌ No | Shares cookies but doesn't auto-add CSRF token to form data |

### Fix Summary:
1. `settings_remove_media_directory`: Replace `request.post()` with browser UI interaction
2. `settings_change_language`: Replace `page.request.post()` with `page.evaluate(() => fetch(...))`
3. `sandboxRoot`: Make locale-independent — match code text ending with `/yaffo.db` instead of English label "Database Path:"

### Round 3: Timeout in settings_add_media_directory
The `sandboxRoot()` helper uses `getByText('Database Path:', { exact: true })` to find
the database path. When the locale is changed to Spanish (and not restored due to the
CSRF bug in round 2), the label is translated ("Ruta de la base de datos:") and the
exact English text match times out. Since the locale persists in the database across
runs, the next run starts with Spanish locale and fails immediately.

Fix: match the `<code>` element whose text ends with `/yaffo.db` — this is
locale-independent and also naturally excludes the Task Queue Database Path
(`/yaffo-queue.db`).
