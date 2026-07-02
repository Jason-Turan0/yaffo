# Themes Tests — Current State (2026-07-02)

## Status
- Generated from `yaffo_ui_tests/specs/themes.yaml`; 6/6 green; typecheck passes.
  Serial — mutates global theme state (default theme, custom theme rows) and
  restores everything it changes.

## Page Map
- `/themes` redirects to the default theme's page. Sidebar `.themes-sidebar` with
  "System"/"Custom" `h3` + `ul.panel-nav`; exactly one `.theme-nav-default` badge.
  Seeded custom theme: **Test Ocean** (`test-ocean`).
- Create: `#new-theme-button` → `#newThemeModal` → `#new-theme-label` → submit →
  redirect to `/themes/<slug>` (slug from URL). New themes start ACCEPTED with an
  empty published token block and no draft.
- Custom-only actions: `#rename-theme-button` (`#renameThemeModal`, input pre-filled;
  rename re-derives the slug → redirect to new URL), `#delete-theme-button` (global
  confirm dialog → hidden `#delete-theme-form` POST → redirect to index). System
  themes render neither.
- "Make default" posts `/themes/<slug>/default` → 204 + HX-Refresh; `html[data-theme]`
  changes on every page. **Restore the original default** (POST works from
  page.request) or every other suite renders under the test theme.

## Chat Simulation (no AI key in sandbox)
- Shared chat-dialog component: ids `#theme-chat-message`, `#theme-chat-form`,
  `#theme-chat-status`, `#theme-chat-messages` (`.chat-message-user/-assistant`).
- Intercept POST `/themes/<slug>/chat` → 202 `{slug}` and GET `/themes/<slug>/status`
  → keep 2 polls IN_PROGRESS (1.5s apart) then READY. Terminal non-FAILED status
  triggers `window.location.reload()` — register `page.waitForEvent('load')` BEFORE
  submitting the message.
- After the reload the server truthfully has no draft (nothing really generated), so
  the create test only asserts the editor re-renders; the draft UI is covered by the
  injected-draft test below.

## Draft Injection (publish/discard coverage)
- Theme storage: one ApplicationSettings row `custom_theme:<slug>` holding
  `json.dumps(asdict(CustomTheme))` — fields slug, status, label, conversations,
  published_theme/working_theme (`{tokens_css, skin_css, favicon_svg,
  placeholder_svg}`). `tokens_css` MUST contain a `[data-theme="<slug>"]` block.
- Inject a finished generation by updating that row via `../venv/bin/python` +
  stdlib sqlite3 (WAL DB shared with the live server; timeout=30): set
  `status="READY"` and a `working_theme` with a distinctive marker color.
- Page then shows `.theme-draft` ("unpublished design…") with **Save draft** /
  **Discard** buttons (HX-Refresh on both).
- Verify through `/themes/<slug>/preview.css`: it serves the **draft while one is
  pending**, else the published CSS — so only read it when no draft pends. After
  publish the marker appears; after discarding a second draft the first marker
  remains and the second never appears.

## Hazards
- Changing the default theme mid-run restyles every parallel suite's pages (visual
  only, but `data-theme` assertions elsewhere would break). Keep the window short
  and always restore in `finally`.
- Theme deletion via `page.request.post('/themes/<slug>/delete')` is the cleanup
  path; `themes_index` 404s nothing (built-ins always exist).
