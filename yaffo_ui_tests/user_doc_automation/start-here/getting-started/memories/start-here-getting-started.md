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


- 2026 run: 2453 device px (0.0418%) differ, all in one 365x27 box on the File
  Information FOLDER value: the docs fixture path flips /tmp (Linux) <-> /private/tmp
  (macOS), so the string and its wrap point move. Same class as settings-overview's
  `#current-thumbnail-dir` leak. The sibling library-basics/photo-details shot already
  ignores `.detail-section:first-child .detail-item:nth-of-type(2) .detail-value`;
  this shot did not -> environment_instability, promote (0.0418% is under the 0.1%
  gate, frame unchanged, photo/people(3)/faces(3)/labels(2) pixel-identical, prose
  unaffected), and the same ignoreRegion was added here so it stops recurring.

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
- 2026 run: 3197 device px differ (0.0607%), all in one 426x154 box inside the Thumbnail
  Directory section. Two changes only: the displayed current thumbnail path lost the macOS
  `/private` prefix (`/private/tmp/yaffo-docs/thumbnails` -> `/tmp/yaffo-docs/thumbnails`) and
  the thumbnail total moved 466.02 KB -> 465.97 KB. Same `.settings-section` slicing (first
  three sections: Media Directories, Thumbnail Directory, Language), same 96 files, layout
  unchanged -> environment/fixture, not the product (the dependency diff that run touched
  Index Photos / media view, not Settings) -> promote; 0.06% is under the 0.1% gate.
- Root cause of the noise: the walkthrough ignores `.media-dir-path` for the /tmp
  canonicalization but not the sibling `#current-thumbnail-dir` <code>, so the platform
  difference leaked into the diff. Extended `ignoreRegions` to cover it.
- Watch: `#thumbnail-size` (generated-thumbnail bytes) is fixture-derived and moved by
  0.05 KB between platforms. Left unmasked on purpose (it is real content); if it jitters
  run after run on a single platform, pin or mask it rather than chasing a baseline.
- Caption was stale independently of the diff: it said "Language, Units, and Media
  Directories" while the shot (before and after) shows Media Directories, Thumbnail
  Directory, Language — the setup drops `sections.slice(3)`, i.e. Units never appears.