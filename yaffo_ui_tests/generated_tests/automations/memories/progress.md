# Automations Tests — Current State (2026-07-02)

## Status
- Generated from `yaffo_ui_tests/specs/automations.yaml`.
- TypeScript validation passes with `cd yaffo_ui_tests && npm run typecheck`.
- Playwright runtime passes twice against the isolated runner:
  `npm run isolatedEnvironment:start` then
  `BASE_URL=http://127.0.0.1:5002 npx playwright test generated_tests/automations/automations.spec.ts`
  → 9 passed on 2026-07-02 (~15s per run; repeatable back-to-back).
- The suite is serial (`mode: 'serial'`): it creates one shared custom automation via
  the New-automation modal in the first test and deletes it in the last; `afterAll`
  falls back to `POST /utilities/automations/<slug>/delete`.

## Sandbox Inventory (seed_database.py)
- System automations: `file_sync` (enabled, schedule `0 * * * *`), `auto_assign_faces`
  (enabled, event, config `threshold` + `assign_multiple_matches`), `export_photo_tag`,
  `assign_location_name`, `geotag_from_neighbors` (disabled — safe for the
  enable/disable toggle test), `classify_labels`, `duplicate_scan` (disabled).
- Seeded custom automation: `file-favorite-kid-photos` — disabled, no trigger,
  published `_FILE_KIDS_CODE`, and a two-round conversation refining the request.
  It finds Maya/Theo photos through assigned faces, keeps favorites, files each
  photo once under `<child>/<year>`, batches moves, and reports progress.
- No AI API key in the sandbox: the chat generation endpoints must be intercepted
  client-side (`page.route`); the Starlark dry-run (`/test-files`) works for real.

## Pitfalls Learned While Writing
- The seeded custom automation intentionally starts disabled with no trigger; the
  UI test opens and cancels the Run scope picker, confirms the empty trigger list,
  and verifies that no run is added to history.
- The Enable/Disable button label has surrounding whitespace/newlines; a
  `hasText: /^(Enable|Disable)$/` filter matches nothing. Use `/^\s*(Enable|Disable)\s*$/`.
  The toggle responds 204 + `HX-Refresh: true` → full page reload.
- **Toggle race (intermittent, seen under full-suite parallel load):** after the
  HX-Refresh reload, the label assertion can pass as soon as the new DOM renders,
  but htmx re-binds its handlers on DOMContentLoaded — a second click fired before
  the document's `load` event can hit an unbound button and silently do nothing
  (no POST, label never flips, and the automation is left toggled). Fix: wrap each
  toggle in `waitForResponse` for the `/enabled` POST, assert the flipped label,
  then `page.waitForLoadState('load')` before any further click (see
  `toggleEnabledTo` in the spec).
- In the trigger editor, the open panel **replaces** the Add-a-schedule/Add-an-event
  buttons (`.adding-schedule`/`.adding-event` on `.automation-trigger-add`), so you
  cannot click `.js-add-event` while the schedule panel is open — click the panel's
  `.js-cancel` first. "One panel at a time" is asserted via container classes and
  `.schedule-editor` visibility.
- The "Edit triggers" link href ends in `/triggers/edit`, so `a[href$="/edit"]` also
  matches it; select the custom-only Edit link with
  `getByRole('link', { name: 'Edit', exact: true })`.
- The "No automation selected" empty state only renders when the DB holds zero
  automations; the seeded system automations make it unreachable (the index route
  redirects to the first automation). Deliberately not asserted.
- `automations_index` redirects to the first automation **by name**; capture the
  created automation's slug from the post-create redirect URL instead of guessing.

## Selector Map
- Sidebar: `nav.utilities-sidebar` filtered by `h2` "Automations";
  `h3:has-text("System") + ul.panel-nav` / `h3:has-text("Custom") + ul.panel-nav`.
  New automation: `#new-automation-button` → `#newAutomationModal` (`.active` class),
  `#new-automation-name`, `button[type="submit"]`.
- Detail actions: `.automation-actions`; `.js-run-files` "Run…";
  `#configure-automation-button`
  (only with config fields); `#edit-automation-button`/`#delete-automation-button`
  (custom only). Run history: `#automation-runs` / `.automation-run-row` (self-polls 5s).
  Run history is capped at the **10 most recent** jobs — on a long-lived environment a
  row-count comparison saturates and never increases; detect a new run as an
  innerText change of `#automation-runs` instead (see automations_run_now).
- Trigger editor: `#automation-triggers`, `.js-add-schedule`, `.js-add-event`,
  `.js-save-schedule`, `.js-cancel`, `.schedule-editor-error`, rows
  `.automation-trigger-row` with `.automation-trigger-kind`, `.automation-trigger-desc[data-cron]`
  (plain-language text filled client-side), `.automation-trigger-event`; row `.btn-danger` deletes.
- Cron builder (plain `<select>`s, safe for `selectOption`): `.cron-mode`
  (`preset|custom`), `.cron-cadence` (`hourly|daily|weekly|monthly|advanced`),
  `.cron-raw`, hidden `[name="cron"]`, `.cron-preview`. Default preset `0 * * * *` →
  preview "Every hour"; `*/30 * * * *` → "Every 30 minutes". Advanced input is
  validated server-side (debounced 250 ms) and gates the Save button.
- Folder picker: `#folder-picker-modal` (`.active`), `#folder-picker-path`,
  `#folder-picker-select` (select current folder; opens at the sandbox media dir),
  `#folder-picker-cancel`.
- Dry-run result: `#automation-test-result`; `.automation-test-meta` ("Testing on …",
  "Ran working|published code · N photos"), heading "Actions (N)", grouped rows with
  `.automation-test-count` "× N" + `.automation-test-group li`; `.test-action-detail`
  and `.automation-test-output` carry `.automation-test-advanced` and are hidden until
  the "Show details" checkbox (`.automation-test-toggle input`) adds `.show-details`;
  failures add `.is-error` + `.automation-test-error`.
- Chat dialog (editor): `#automation-chat-message`, `#automation-chat-form`,
  `#automation-chat-status` (busy bar), `#automation-chat-messages` with
  `.chat-message-user` / `.chat-message-assistant`. Poll interval 1.5s; a terminal
  status (anything but IN_PROGRESS, except FAILED stays open) triggers
  `window.location.reload()` — register `page.waitForEvent('load')` BEFORE submitting,
  and keep ≥2 IN_PROGRESS polls so the in-flight assertions have time to run.
- Configure modal: `#configureAutomationModal` (`.active`), `#config-threshold`,
  `#config-assign_multiple_matches`; cancel via `.modal-actions [name="cancel"]`;
  save redirects back to the detail page.
- Confirm dialog: `#global-confirm-dialog` (`.active`), `#confirm-dialog-confirm`.
- Notifications: `.notification.visible`. Key string:
  "Run started — it will appear in Run history when it finishes."
