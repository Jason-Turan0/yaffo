# create-customize/importing — notes

## new-import-sync.webp — 2026-10 recapture (accepted as intended change)
- Diff: 1928 px (0.097%), bbox 1064x491 at (0,137) of a 532x932 shot. Every changed
  pixel is outside the white panel: the crop made by `clip: #newAutomationModal
  .modal-content` leaves ~15px of the dimmed Automations page visible on the left,
  right and top edges, and the page content behind the fixed modal shifted
  vertically by a few pixels. The dialog itself is pixel-identical (same title,
  same Custom / Import from… / Send to… / Add a new service… cards, same
  Folder / Immich / OneDrive service cards, same "Camera card" name, same footer).
- Cause is in the shell, not this page: `base.html` now renders
  `#navbar-pages-bar` with `hidden` unless `show_pages_bar`, and
  `yaffo/utils/settings.py:get_show_pages_bar` returns True only when the
  ApplicationSettings row exists and is "true" — i.e. the Pages strip is HIDDEN by
  default, where nav.js previously defaulted to visible from empty localStorage.
  nav.js now PUTs to `APP_CONFIG.urls.pages_bar_visibility`, so strip visibility is
  an app-wide *server* setting instead of per-browser localStorage.
- Consequence for this page: the "Importing Photos" prose never mentions the Pages
  strip, and the shot's subject (the New automation dialog) is unchanged, so no
  prose or walkthrough edit was made. A background-only diff on this shot is not a
  walkthrough defect and not a regression.

## Watch-outs for re-captures
- Any shot whose crop includes page chrome around the clipped element now depends
  on the global `show_pages_bar` setting. Nothing in this walkthrough (or in
  custom-pages) clicks "Pages ▾", so the state stays put — but do not add a click on
  `#nav-pages-toggle` here: it persists server-side and would shift the backdrop of
  every other page's shots too.
- All four shots build a disposable Folder import and delete it in teardown; none
  turns the sync on, so `#sync-run-now` (shown only while a sync is on) must stay
  absent — the flows assert `toHaveCount(0)`.
