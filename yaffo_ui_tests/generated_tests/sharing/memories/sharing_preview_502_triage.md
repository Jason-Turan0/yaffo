# Triage: sharing_grant_a_media_directory_and_browse_it — preview 502

## Failure
Test asserts a single raw HTTP GET to B's preview proxy returns 200:
`const previewResponse = await pageB.request.get(previewSrc!, { failOnStatusCode: false }); expect(previewResponse.ok()).toBe(true)`
Got 502 for `/sharing/devices/52HM-.../preview?media_item_id=31&v=...`.

## Evidence
1. `routes/sharing.py` `sharing_device_preview` aborts 502 on CallError/P2PServiceError (transport failure OR peer returned non-ok), 504 on timeout. So 502 = transient p2p call failure, NOT necessarily an auth/app bug.
2. `static/sharing/remote_gallery.js` top comment explicitly says firing many preview p2p calls at once "surfaced as 502s"; the app therefore uses a 4-concurrency queue + 2s delayed retry + placeholder fallback. Preview 502s under contention are a KNOWN, tolerated condition.
3. `list_files` succeeded in the same test (grid rendered, total>0) using the SAME grant authorization path (`granted_media_query`), so the media-dir grant itself works; item 31 was listed and is granted.
4. Media dir contains 3 videos (2 root mp4s + boy-and-the-waves.mp4) but media_item_id=31 is a photo (2015 lakefront / 2026 family-at-home depending on id order); preview_jpeg_bytes handles PNGs fine → no deterministic photo-preview bug.
5. Run history: the preview assertion PASSED in the two prior runs (those failed on a different, already-fixed test `sharing_revoke_a_device`). So the preview endpoint works; this 502 is transient contention/timing (background indexing tasks + the browser's own lazy queue firing previews concurrently with the test's raw request).

## Classification
test_code_defect. Spec goal = "gallery shows previews of A's photos", which the app achieves via lazy queue + retry + fallback. The test's single-shot strict-200 assertion is stricter than the app's documented contract and bypasses its own retry protection.

## Suggested action (implemented)
Add an `expectPreviewServes(page, previewSrc)` helper that polls the preview endpoint, retrying transient 502/504 with 1s backoff until a 20s deadline, and asserts a non-empty body on success. Replace the single-shot strict-200 assertion in `sharing_grant_a_media_directory_and_browse_it` with a call to it.
