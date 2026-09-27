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
- 2026 run (2453 px = 0.0418%, box 365x27 at (120,435), frame 2784x2108, no bottom
  band): the *other* known leak — the FOLDER path spelling (`/private/tmp` on macOS vs
  `/tmp` on Linux), same cause as settings-overview's `.media-dir-path`. Localized to
  one text line, meaning unchanged, no prose describes the path -> environment_instability
  + promote, and `ignoreRegions` extended with
  `.detail-section:first-child .detail-item:nth-of-type(2) .detail-value`
  (the selector library-basics/photo-details already uses for the same view).
- The dvh frame-growth signature above is a separate, still-unfixed issue: this run did
  not show it, so the two must be told apart by whether the diff box sits at the bottom
  edge of the frame (frame rounding) or inside the sidebar text (path spelling).


## settings-overview.webp
- 2026 run: 3197 px differ (0.0607% of 1392x946), localized to the Thumbnail
  Directory block only. Frame, sections, layout and the Files count (96) are
  identical; the two text runs that moved are the tmp path
  (`/private/tmp/yaffo-docs/...` on macOS vs `/tmp/yaffo-docs/...` on Linux) and
  the generated thumbnail total (466.02 -> 465.97 KB).
- `.media-dir-path` was already ignored for exactly this reason; the
  thumbnail-directory path (`.current-path-item code` / `#current-thumbnail-dir`)
  was not, so the same platform canonicalization leaked into the diff. Extended
  `ignoreRegions` to cover it. The size digits are generated-thumbnail jitter,
  inside the 0.1% promote gate -> environment_instability + promote.
- Prose: the alt text claimed the shot shows "Language, Units, and Media
  Directories"; the walkthrough keeps only the first three `.settings-section`s
  (Media Directories, Thumbnail Directory, Language) and Units is stripped, so
  the caption was corrected.

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