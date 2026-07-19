# Custom Pages Tests — Current State (2026-07-02)

## Status
- Generated from `yaffo_ui_tests/specs/custom_pages.yaml`; 6/6 green; typecheck
  passes. Serial; every created page is deleted (afterAll sweeps leftovers via
  `POST /pages/<id>/delete`).

## Navigation Facts
- The pages bar in the navbar is **expanded by default** in a fresh browser context;
  the "Pages" nav button is a collapse TOGGLE (`#nav-pages-toggle`, localStorage
  `yaffo.pagesBarHidden`) — clicking it first HIDES `.nav-new-page` and the page
  links. Don't click it.
- "New page" is a plain form POST to `/pages` (`.nav-new-page`); `pages_detail`
  redirects widget-less pages to `/pages/<id>/design`.
- Direct Playwright request helpers first GET `/` and submit the rendered
  `csrf_token`; browser-driven forms and fetches receive it through the app UI.
- The Bennett sandbox seeds one published page: "Florida Trip" (`/pages/1`),
  with a full-width Hero banner above a full-width Photo gallery. The Obama peer
  does not seed a custom page because it has no Florida-trip fixture. Its design
  view has two user/assistant rounds showing the initial request and the refinement
  that removed the redundant page heading and constrained the gallery.

## Design View (`/pages/<id>/design`, .page-design)
- Metadata: `#page-title` (placeholder/default "Untitled Page"), `#page-subtitle`,
  `#page-tab-order`, `#page-show-title`. **tab_order is a position** among nav pages;
  the server repositions and clamps it (saving 7 with 3 pages reads back 3) — use a
  valid position for round-trips.
- Buttons: `#add-widget-button`, `#save-page-button` (idle: POST `/update` 204 then
  navigate to detail; READY review: publishes), `#delete-page-button` (global
  confirm naming the title → hidden form POST → redirect `/`).
- Widgets: `.grid-stack-item[gs-id]` with `.widget-header`, `.widget-title` span
  (default **"New Widget"**, capital W), hidden `.widget-title-input`, `.widget-edit`
  pencil (reveals input; Enter commits), `.widget-delete`. Manual adds render via the
  real `/widgets/preview` route; nothing persists until Save.

## AI Generation (simulated — no API key in sandbox)
- Chat dialog id `conversation`: `#conversation-message/-form/-status/-messages/-cancel`.
- Intercept `POST /pages/<id>/chat` → 202 `{version_id}` (any fake id) and
  `GET /pages/<id>/versions/<vid>/status` → payload shape
  `{version_id, status, started_at, completed_at, error, messages, widgets}`;
  2 polls IN_PROGRESS then READY. Widgets entries
  (`{id,title,data_query,state,html,css,js,grid_x,grid_y,grid_w,grid_h}`) are
  rendered client-side through the REAL preview route — works with a fake version.
- Running phase: `.page-design.is-generating`, grid static, `#add-widget-button`
  disabled, `#conversation-status` visible, feed shows user/assistant bubbles.
- READY review: widget appears (`.grid-stack-item[gs-id="<id>"]`), grid unlocks,
  Save enabled (publish → intercept `/versions/<vid>/publish` → 204 → client
  navigates to detail). **`#conversation-cancel` is DISABLED on READY** — cancel is
  only enabled while IN_PROGRESS or FAILED (grid.js refreshUi).
- Unlike themes/automations chats there is **no page reload on settle** — the grid
  reconciles widgets in place from the poll payload.

## Presentation View (`/pages/<id>`, .page-presentation)
- Grid carries `.grid-stack-static`; widgets load `.widget-frame` iframes (saved
  widgets by src route, drafts by srcdoc); no `.widget-edit`/`.widget-delete`.
- `page_header` (title/subtitle) renders only when show_title is on.
