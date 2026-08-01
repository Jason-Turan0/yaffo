# Fix Plan: themes_set_default_theme CSRF token issue

## Changes needed:

### 1. deleteThemeViaApi (line ~52)
The function calls `page.request.post` without CSRF token. When called within tests
(page is loaded), it silently fails (403). Fix: extract CSRF token from page when
available and include as `X-CSRF-Token` header.

### 2. afterAll (line ~93)
Creates a new page without navigating, so no session/CSRF token exists. Fix: navigate
to `/themes` first to establish session, extract CSRF token, then use it for deletions.

### 3. themes_set_default_theme finally block (line ~225)
`page.request.post('/themes/${originalDefault}/default')` lacks CSRF token, so the
default restore never happens. Fix: extract CSRF token from page and include it
as `X-CSRF-Token` header.

## Root cause:
CSRF protection (`protect_unsafe_request` in security.py) blocks all non-safe
requests without a valid `X-CSRF-Token` header. htmx requests (from page interactions)
include this header automatically (configured in security.js), but `page.request.post`
does not.
