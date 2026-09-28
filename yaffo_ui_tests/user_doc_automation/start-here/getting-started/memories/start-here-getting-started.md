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
- Recurrence: same +14 device px bottom band seen again on a later run (now with a large
  dependency diff on the page: media/view.css, responsive.css, index.css, nav.js...). Geometry
  is still dvh/viewport-derived, content inside the overlap still pixel-identical, so still
  fixture/frame, not the product change the diff touches -> quarantine, still needs a pinned crop.
- Prose unaffected: the section text lists preview / file info / capture date+device /
  location / people / faces / labels, all still visible; nothing describes the tags
  section.
- Later run: 2453 px differ (0.0418%) in a single 365x27 device-px box at (120,435),
  i.e. CSS (60,217) 182x13.5 = one text line at the sidebar's text left edge. Working
  the box positions (card padding 20 + content padding 20, h2 18px, h3 14px, labels
  13px @ lh 1.4) lands on the FOLDER value's FIRST line: `/tmp/yaffo-docs/Family`
  (Linux) vs `/private/tmp/yaffo-docs/Family` (macOS). The break stays at the space
  before `Photos/...`, which is why line 2 never changes and the diff box is one line
  tall, not two.
- Not a product change and not a capture error: the host path spelling again. The
  library-basics/photo-details walkthrough documents this exact view and already masks
  it, so this shot now carries the same ignoreRegions entry
  (`".detail-section:first-child .detail-item:nth-of-type(2) .detail-value"`) — mask,
  never adopt, because the next host flips it back.
- Still outstanding here: the dvh-derived frame growth (+7 CSS px bottom band, above).
  It did not recur in this run (no bottom band in the diff), so no new baseline; if it
  returns, pin the pane height instead of adopting the frame.


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


## settings-overview.webp
- 2026 run: 3197 px differ (0.0607% of the 1392x946 CSS-px frame), one 426x154 box at (142,1052) = the
  `#current-thumbnail-dir` value plus the `#thumbnail-stats` bar. The three kept `.settings-section`s
  (Media Directories / Thumbnail Directory / Language), the clip and the framing are pixel-identical.
- Two causes, both fixture/host, not product: the path rendered `/tmp/yaffo-docs/thumbnails` where the
  baseline had `/private/tmp/...` — the very macOS/Linux /tmp canonicalization the shot already masks for
  `.media-dir-path`, but the thumbnail path was not in the mask — and `Total Size` drifted
  466.02 -> 465.97 KB (`Files:` stayed 96), a number computed by counting the generated thumbnail cache,
  so it moves whenever the fixture is rebuilt.
- Fix applied: ignoreRegions extended to `#current-thumbnail-dir` and `#thumbnail-stats`, so the shot no
  longer flips on host canonicalization or fixture rebuild. The paths/numbers stay visible in the image.
- Prose bug found while checking this shot: the alt text claimed "Language, Units, and Media Directories",
  but the setup removes every `.settings-section` after the third one, so Units (section 4 in
  `templates/settings/index.html`) can never appear. Alt text now names Media Directories, Thumbnail
  Directory, and Language.