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

## Watch-outs for re-captures
- `.widget-order-controls` is `pointer-events: none` until the card is hovered or
  `is-direct-controls` is set; a flip book click on `.widget-order-*` silently misses
  unless the card is hovered first.
- The clipboard/Pages strip can be collapsed via the "Pages ▾" toggle (localStorage),
  so shots that need the strip must ensure it is expanded.
