# library-basics/photo-details — notes

## media-detail.webp framing
- Shot clip is `.photo-viewer`, which is a viewport-bounded pane
  (`height: calc(100dvh - 60px)`) whose vertical extent in the capture depends on
  how much chrome (navbar + page padding) sits above it.
- 2026-09 recapture: image grew 2784x2292 -> 2784x2306 and the reported diff was
  exactly 2784 x 14 px, i.e. every pre-existing pixel matched after top-left
  alignment; only a blank strip of page background was added at the bottom.
  No panel, control, label, chip, face or count changed -> benign reframe from
  the responsive/nav CSS pass (view.css, base.css, nav.css, base.html). Adopted.
- A height-only reframe with a pixel-identical body is NOT worth blocking the
  docs for; check the diff count against width x extra rows before treating it
  as content drift.
- ignoreRegions on `.detail-section:first-child .detail-item:nth-of-type(2)
  .detail-value` masks the whole folder line, so the /private/tmp (macOS) vs
  /tmp (Linux) host spelling never appears in the diff count. Keep it.
- The fixture intentionally has no non-playable video container; the
  "Open in default player" branch of the prose is covered only by view.html.
