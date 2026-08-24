# Triage: sharing_grant_a_media_directory_and_browse_it preview 502

## Facts
- Failing assertion (line 247): single non-retried `pageB.request.get(previewSrc)` must be 2xx.
- Preview URL hits B's `/sharing/devices/<id>/preview` route (routes/sharing.py), which proxies
  a live p2p `pull_preview` call to A and `abort(502)` on `CallError`/`P2PServiceError`.
- p2p LAN calls use `LAN_CALL_TIMEOUT_SECONDS = 1.5` (service.py). Preview generation decodes a
  ~2MB PNG and recompresses (utils/image.py `preview_jpeg_bytes`).
- `static/sharing/remote_gallery.js` explicitly documents transient 502s from concurrent/timeout
  preview calls and loads previews via queue (max 4) + one delayed retry before fallback.
- The actual UI therefore tolerates a first-request 502; the test does not.
- Run history: suite passed 12/12 on 2026-07-18; 2026-08-01 failed a different test (delete race,
  later fixed); 2026-08-24 failed this preview assertion. Intermittent.

## Classification
test_code_defect — the test asserts single-shot preview success, which is stricter than the app's
documented behavior (retry on transient 502). Spec goal (gallery shows previews) is achievable via UI.

## Suggested fix
Retry the preview GET a few times, or wait for the rendered `.remote-photo-card img` to finish
loading (`preview-pending` removed / `data-preview-state="done"`).
