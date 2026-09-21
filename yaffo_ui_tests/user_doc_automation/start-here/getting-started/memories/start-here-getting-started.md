# start-here/getting-started — shot notes

## media-detail.webp
- Clip `.photo-viewer`, whose height is `calc(100dvh - 60px)`
  (`yaffo/static/media/view.css`), so the captured frame is viewport/dvh-derived and
  gets capped by the visible viewport.
- 2026 run: recapture 2784x2092 -> 2784x2106 (+14 device px = +7 CSS px). 39181 px
  differ, but 2784x14 = 38976 of them are the new bottom band; only ~200 px differ
  inside the overlap (the "No tags" placeholder at the sidebar's bottom edge, which was
  cut off before). No dependency in `getting-started.lock.json` changed, and the photo,
  file info, location, people (3), faces (3) and labels (2) are pixel-identical.
- Same signature as organize-review/locations `locations-map.webp` (3192 -> 3206, thin
  bottom band): viewport/100dvh frame rounding, not a product change -> quarantine.
  A reframed shot, so the "tiny + not reframed" promote exemption does not apply.
- Watch for recurrence: if the 7 px frame growth returns run after run, the pane height
  is not reproducible and the shot needs a fixed crop (e.g. clip a wrapper with a pinned
  height) rather than a new baseline.
- Prose unaffected: the section text lists preview / file info / capture date+device /
  location / people / faces / labels, all still visible; nothing describes the tags
  section.


## gallery-home.webp
- 2026 run: 12618 device px differ (0.2836% of the 2784x1600 frame), all inside one 242x82
  device box at (2468, 73) = the header's Grid|Timeline `view-toggle` (`yaffo/templates/index.html`
  + `.view-toggle` in `yaffo/static/index.css`). Frame size, clip, grid, sidebar, subtitle count
  ("Showing 25 of 32 photos") and both card rows are pixel-identical; the labels and the active
  (Grid) state match too.
- No dependency hash in `getting-started.lock.json` changed, so the same templates + CSS rendered
  this one control a pixel or two differently: the pill is right-aligned and content-sized, so its
  width and edges follow text metrics. Not a product change, not a capture error, not a broken
  render -> environment_instability.
- 0.2836% is ~3x the 0.1% "tiny variation" promote gate and the whole control repainted, so do not
  adopt: quarantine. If the toggle keeps jittering run after run, pin the header metrics (fixed-size
  view-toggle / explicit crop) instead of chasing a baseline.