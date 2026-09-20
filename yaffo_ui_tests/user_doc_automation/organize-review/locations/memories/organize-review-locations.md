# organize-review/locations — shot notes

## locations-map.webp
- Clip `.main-container-layout`; the live OSM map (`#map`, `height: calc(100vh - 200px)`,
  `yaffo/static/locations/list.css`) is masked via `ignoreRegions: [".ol-viewport"]`, so
  tile differences never register — only the frame geometry and the unmasked strip
  outside the baseline mask do.
- 2026-09 run: frame height drifted 3192 -> 3206 device px (1596 -> 1603 CSS; ~9296 px
  differ, all in a thin band at the bottom edge). Sidebar, header, cluster labels
  (18 Chicago / 13 Siesta Key) and map extent were pixel-identical; no dependency in
  `locations.lock.json` changed, so this cannot be a product change. Treated as
  environment/layout drift of the captured frame -> quarantine (a reframed shot, so the
  "tiny + not reframed" promote exemption does not apply).
- Watch for recurrence: if the same 7 px frame growth comes back run after run, the page
  height is not reproducible (viewport/100vh rounding) and the shot needs a different crop
  strategy (e.g. clip `#map` + fixed height), not a new baseline.
- RECURRENCE confirmed (next run): 3192 -> 3206, exactly 9296 px differ, again a thin band
  at the bottom edge only; header, sidebar, cluster labels (18 / 13) and map extent again
  identical, no dependency hash change. Same classification (environment_instability) and
  same action (quarantine) — do NOT adopt as baseline. Because the drift now repeats
  byte-for-byte, treat the frame height as non-reproducible and change the capture: clip
  `#map` (or the layout with an explicit height) so the shot no longer inherits
  `100dvh - 200px` rounding.
