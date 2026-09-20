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
