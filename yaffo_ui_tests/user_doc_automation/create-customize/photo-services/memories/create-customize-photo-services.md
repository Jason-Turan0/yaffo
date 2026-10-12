# create-customize/photo-services

Shot asset: docs/guide/create-customize/assets/photo-services/new-photo-service.webp

## 2026-10 recapture (accepted as intended change, promoted)
- The diff was backdrop-only: the New automation dialog itself was
  pixel-identical. The changed pixels (0.24%, bbox 1064x348 starting at y=17)
  were the page content visible in the margin the capture adds around the
  clipped modal, plus two small left-edge fragments at the same heights.
- Cause: the Pages strip became an app-wide, server-persisted preference.
  base.html renders `hidden` on #navbar-pages-bar and `collapsed` on
  #nav-pages-toggle from `show_pages_bar`; nav.js applies `bar.hidden` on load
  and PUTs /pages/bar-visibility on toggle; yaffo/utils/settings.py
  get_show_pages_bar() returns False when no ApplicationSettings row exists, so
  the strip is collapsed by default. With the strip gone the navbar is ~45px
  shorter and the automations page behind the dialog sits higher — a vertical
  page shift that an element-clipped shot (service-builder.webp) does not see,
  but a modal-clipped one does.
- Do not read a backdrop-only diff on this page as a walkthrough defect or an
  app regression: the dialog keeps its content and framing either way.
- The walkthrough now pins the strip collapsed before each shot and puts back
  whatever it found afterwards. Watch-out: the strip is global state, so a page
  that needs it (custom-pages clicks “+ New page” on the strip, and the guide
  prose there says pages appear on it) will now have to expand it itself.
