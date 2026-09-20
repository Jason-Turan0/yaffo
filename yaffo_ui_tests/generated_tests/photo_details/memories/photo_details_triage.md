# photo_details responsive face-highlight triage
- view.js implements tap-to-highlight via click listener (initializeFaceTapHighlighting) and resize redraw via updateCanvasSize.
- Inline onmouseenter/onmouseleave on .face-thumbnail call highlightFace/clearHighlights.
- Failing tests use withTouchContext (hasTouch+isMobile) + locator.tap().
- Failure: tap transiently highlights then clears (mouseenter/mouseleave emulation), so class assertion misses it (test 1) or class passes but canvas already cleared (test 2).
- Classification: test_code_defect — app supports the spec goal via its click handler; the emulated tap path doesn't leave the highlight set.
