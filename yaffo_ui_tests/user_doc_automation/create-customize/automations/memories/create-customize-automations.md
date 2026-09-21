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
- There is NO "Run now" control anywhere in the app (grep of templates, en.json
  and automations.js): the manual run button label is "Run…" (class `.js-run-files`,
  route automations_run_now). The guide used to call it "Run now" — fixed.
