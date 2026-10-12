# create-customize/automations — notes

## automations-list.webp (verified at 937def53a1b4)
- Shot is framed at 1440x1000 on /utilities/automations/file-favorite-kid-photos
  with the reviewed "File favorite kid photos" custom fixture; 7 system
  automations + Custom group + ON badges for 5 of them.
- Known content change after the responsive/CSS commit: the sidebar label
  "Assign location na…" now wraps to "Assign location name" on two lines
  (utilities/_base.css: `#automations-nav .panel-nav-label {white-space: normal;
  overflow-wrap: anywhere}`). Layout of everything else is unchanged.
- This shot also shows broad, whole-page anti-aliasing churn in diffs (2.3% of
  pixels, bbox over the entire page) even when nothing moves — treat plain
  glyph-edge magenta with no position/wording change as renderer noise, not a
  state change.

## UI names checked against the templates (2026-09)
- Detail header for a custom automation: Enable/Disable, Run…, Edit,
  Edit triggers, Configure (only when config fields exist), Edit details, Delete.
- Editor: Test…, Publish draft, Discard; code toggle is "Working code" / "Active code".
- Triggers editor: "Add a schedule" / "Add an event", "Save schedule", "Cancel".
- CORRECTION (verified in templates/utilities/sync_detail.html): a **Run now**
  control DOES exist for syncs only — `#sync-run-now` (label `Run now`, rendered
  while the sync is on). Automations (system + custom) use **Run…**
  (class `.js-run-files`, route automations_run_now). The guide's line "Use
  **Run…** for system and custom automations, or **Run now** for an enabled sync"
  is CORRECT — do not "fix" it (an earlier note here wrongly claimed no such
  control exists).

## automation-test.webp — shell reframe, not a state change (2244x2006 -> 2244x2064)
- The shots clipped to `.utilities-content` are framed from that element's top down
  to the bottom of the viewport, so their height tracks the navbar height. The
  reviewed diff is exactly 2244 x 58 = 130152 pixels — a bottom-only band of added
  page background; every content pixel (conversation, code, "Actions (5)",
  "Move 0 photo(s)", "Ran published code · 32 photos") is identical.
- Cause: base.html now renders #navbar-pages-bar `hidden` unless the server-side
  `show_pages_bar` setting says otherwise (routes/pages.py,
  PUT /pages/bar-visibility) and nav.js now does `apply(bar.hidden)` instead of
  force-showing the strip from localStorage, so the Pages strip is gone and the
  navbar is one strip (~29 CSS px) shorter.
- Read a bottom-band-only diff here as a shell reframe, not a wrong page/filter or
  broken content, and check the prose for shell-level names before editing (this
  page never mentions the Pages strip).
- Do NOT pin /pages/bar-visibility from this walkthrough to "stabilise" the framing:
  it is shared fixture state that other pages' clip framing (custom-pages) and that
  walkthrough's `.nav-page-tab` locators also depend on.
