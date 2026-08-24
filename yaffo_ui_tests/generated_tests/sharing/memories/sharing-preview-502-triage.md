# sharing_grant_a_media_directory_and_browse_it — preview 502 triage

## Failure
`expect(previewResponse.ok()).toBe(true)` after `pageB.request.get(previewSrc, {failOnStatusCode:false})` returned 502 for `/sharing/devices/GP52-XA5F-4RHG-FM2V/preview?media_item_id=31&v=…`.

## Root cause
- routes/sharing.py `sharing_device_preview` → `service.pull_preview.send` → abort(502) on CallError/P2PServiceError.
- pull_preview uses a fresh QUIC socket with LAN_CALL_TIMEOUT_SECONDS=1.5s; on timeout falls back to unreachable hub (ws://127.0.0.1:9) → 502.
- remote_gallery.js documents this exact 502 as transient (too many in-flight previews trip call timeouts) and mitigates with bounded concurrency (4) + one retry.
- The test bypasses the mitigation with a single unretried raw fetch, concurrent with the browser's own queued preview loads.
- A's log has NO preview-generation error; list_files/list_shared/pulls all succeed. App is healthy.

## Classification
environment_instability (transient network timeout); test assertion is fragile but not fundamentally wrong.

## Suggested hardening
Wait for `.remote-photo-card img` to actually load (previewState 'done' / naturalWidth>0) via the app's queue+retry, or wrap the raw preview fetch in a small retry loop before asserting 200.
