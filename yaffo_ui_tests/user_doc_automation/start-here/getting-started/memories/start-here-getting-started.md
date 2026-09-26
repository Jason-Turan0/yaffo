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
- 2026 run: 2453 px differ (0.0418% of the 1392x1122 frame), all inside one 365x27 box
  at (120, 435) = the FOLDER value in the sidebar. Baseline showed /tmp/yaffo-docs/...
  and the new capture /private/tmp/yaffo-docs/... — the macOS/Linux /tmp
  canonicalization, same cause as settings-overview.webp, not a product change. This
  shot had no ignoreRegions at all, so the path reached the diff.
- Fixed in the walkthrough instead of chasing a baseline: media-detail.webp now carries
  the same
  `ignoreRegions: [".detail-section:first-child .detail-item:nth-of-type(2) .detail-value"]`
  as library-basics/photo-details `media-detail.webp` (same view, already ignores it).
- Prose unaffected here too: the section names preview / file info / capture date+device
  / location / people (3) / faces (3) / labels (2), all present and identical.


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
- 2026 run: 3197 px differ (0.0607% of the 1392x946 frame), all inside one 426x154 box
  over the Thumbnail Directory block. Two environment causes, no product change:
  (1) the current thumbnail dir `<code id="current-thumbnail-dir">` re-rendered as
  /tmp/yaffo-docs/thumbnails instead of /private/tmp/yaffo-docs/thumbnails — the same
  macOS/Linux /tmp canonicalization the walkthrough already calls benign, but
  `ignoreRegions` only lists `.media-dir-path`, so the second path field leaks it into the
  diff; (2) the streamed `#thumbnail-size` total drifted 466.02 -> 465.97 KB with
  "Files: 96" unchanged (generated-thumbnail bytes).
- Sections (Media Directories / Thumbnail Directory / Language), headings, buttons
  (Remove, Browse..., Add Directory, Change Directory, Save) and placeholders are
  pixel-identical, and nothing in the lock file that renders /settings changed (en.json +2
  = unrelated audit-log strings) -> environment_instability; 0.0607% <= 0.1%, not reframed,
  counts unchanged, no prose affected -> promote.
- If the path noise recurs run after run, extend `ignoreRegions` to `#current-thumbnail-dir`
  (or normalize the fixture path) rather than chasing a baseline. Do not ignore
  `#thumbnail-stats`: that size text is real fixture content.
- Prose: the settings figure's caption named "Units", a section the walkthrough slices off
  (`sections.slice(3)` drops Units and everything after), so the caption was corrected to
  the sections actually shown.