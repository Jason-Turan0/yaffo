# photo_details responsive triage (2026-09-04)

Failing tests (both timed out at 5000ms, the test-runner timeout):
- photo_details_face_highlight_works_with_a_coarse_pointer
- photo_details_face_highlight_survives_rotation

Findings:
- App implements the spec goals: static/media/view.js has initializeFaceTapHighlighting
  (click/tap -> clearHighlights + highlightFace) and updateCanvasSize redraws highlightedFaceId
  on resize. Templates/media/view.html renders #mainPhoto + #faceCanvas + .face-thumbnail.
- routes/media.py builds faces_with_locations (faces with location_top != None) so highlightFace
  can actually draw.
- The only passing touch test in the same file (metadata_actions_are_touch_sized) uses the same
  withTouchContext + findFirstPhotoIdWithDetectedFaces + goto and passes in 506ms; it does NOT
  waitForFaceCanvas/tap/read canvas pixels.
- face_assignment responsive tests use test.describe.configure({ timeout: 90_000 }) for the same
  withTouchContext + tap pattern; this spec does not, so the heavy touch path hits the 5000ms default.
- No background indexing during the run (background_tasks.log idle after 21:29:43).

Classification: test_code_defect (insufficient timeout budget for the touch-context cases; app is fine).

Fix applied:
- test.describe.configure({ timeout: 30_000 }) inside the responsive describe.
- waitForFaceCanvas waitForFunction timeout 15_000.
- expect.poll in face_highlight_survives_rotation timeout 10_000.
