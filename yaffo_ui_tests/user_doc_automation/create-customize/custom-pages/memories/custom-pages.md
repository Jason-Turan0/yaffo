# create-customize/custom-pages

Shot asset: docs/guide/create-customize/assets/custom-pages/custom-page-design.webp

## 2026-09 recapture (accepted as intended change, promoted)
- Diff was canvas-only: the left editor panel was pixel-identical, everything on
  the right (both widget headers + both widget bodies) differed.
- Cause: the responsive overhaul. `yaffo/static/responsive.css` is NEW (it is absent
  from custom-pages.lock.json's observed static list), and `_widget.html` gained the
  `.widget-order-controls` span (up / down / shorter / taller). Those buttons are
  opacity:0 (still in layout, min-height 32px), so the widget header grew without
  any icon becoming visible at mouse-idle. Do not read a canvas-only diff on this
  page as a walkthrough defect.
- Behaviour added by the same work, now documented on the page:
  - canvas bands in `pages/grid.js` (CANVAS_BANDS): >=900px 12 cols, >=600px 6 cols,
    <600px 1 col; Save always writes the 12-column geometry.
  - drag-to-move/drag-to-resize are off on a coarse pointer and on a one-column
    canvas; the explicit buttons are the path there (visible on hover/focus too).

## 2026-09 dependency-only flag (yaffo_ui_tests/scripts/seed_database.py)
- The page was flagged because the docs-fixture seed script's fingerprint changed;
  no shot changed (0 differing pixels, no reframing) and no product code changed
  (routes, templates, static, widget_templates.py all match the lock).
- Classified environment_instability / harmless -> promote, no page edits: every
  fixture fact the page states is visible in the unchanged shots (Florida Trip
  hero + gallery, Maya's ready draft with Party photos + People, Chicago 2015
  "Generating… 2:41", four strip tabs incl. Untitled Page, tab order 4).
- seed_database.py lives in yaffo_ui_tests/scripts/, which is NOT in the readable
  sandbox tree, so its diff cannot be inspected; the lock's `shots` layout dumps
  are the best record of the captured fixture text. If a future flag needs the
  actual seed diff, it must be provided or the tree widened.
- No browser in this runner ("chrome-for-testing is not installed"), so the live
  app at 127.0.0.1:5002 cannot be inspected; rely on the shots + lock.

## Watch-outs for re-captures
- `.widget-order-controls` is `pointer-events: none` until the card is hovered or
  `is-direct-controls` is set; a flip book click on `.widget-order-*` silently misses
  unless the card is hovered first.
- The clipboard/Pages strip can be collapsed via the "Pages ▾" toggle (localStorage),
  so shots that need the strip must ensure it is expanded.
