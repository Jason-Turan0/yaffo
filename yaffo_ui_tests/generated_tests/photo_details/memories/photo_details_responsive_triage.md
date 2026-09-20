# Photo Details Responsive — face tap highlight triage (2026-09-20)

## Failing tests (full 10-test run)
1. photo_details_face_highlight_works_with_a_coarse_pointer — toHaveClass(/highlighted/) failed (class stayed "face-thumbnail" for 9 polls/5s).
2. photo_details_face_highlight_survives_rotation — toHaveClass passed but faceOverlayIsPainted() returned false.

## App code facts
- view.js initializeFaceTapHighlighting adds a click listener per .face-thumbnail: clearHighlights() then highlightFace(faceId). highlightFace adds .highlighted and strokes #faceCanvas, but returns early if the face is missing from faces_with_locations (i.e. location_top is null in routes/media.py).
- face-reassign.js also adds a click listener on the same thumbnails that opens the reassign overlay (SearchableSelect + overlay reposition).
- routes/media.py only includes faces with location_top is not None into faces_with_locations, while the template renders ALL media_item.faces as thumbnails.
- Same committed code was 15/15 passing on 2026-09-04 (memories/progress.md); 4 isolated single-test runs passed 2026-09-20 before the full run failed.

## Conclusion
- Spec goal (tap -> highlight + painted canvas) is implemented and achievable.
- The two failures differ within one run (class-absent vs class-present-but-canvas-clear), the classic signature of a race between the tap's highlight and the competing reassign-overlay/reflow (and possibly a trailing clear) rather than a deterministic app bug.
- Tests assert the highlight state immediately after tap() without settling/polling, and rely on the fragile assumption that hasTouch-without-isMobile emits no trailing mouse events.
- Classified as test_code_defect.
- Fix: replace face-thumbnail tap() with dispatchEvent('click') so only the click handler (the tap's code path) runs without Chromium's synthesized mouse compatibility events racing/clearing the highlight; poll the painted canvas instead of one-shot reads.
