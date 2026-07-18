# Sharing Tests — Current State (2026-07-18)

## Status: PASSING (11/11) against a fresh two-instance sandbox (~3.3m)

## Environment (non-negotiable)
- `npm run isolatedEnvironment:start:sharing` → instance A (seeded library + album) on 5002, instance B (empty peer) on 5003. Both run the p2p engine (LAN/mDNS only; hub `ws://127.0.0.1:9` is deliberately unreachable → hub chip "Disconnected", presence chips "Local").
- Run: `BASE_URL=http://127.0.0.1:5002 PEER_URL=http://127.0.0.1:5003 npx playwright test generated_tests/sharing/sharing.spec.ts`
- THE SUITE IS STATEFUL AND ORDERED (`mode: 'default'`, one worker). Every full run needs a FRESH sandbox — pairing, grants, and B's download directory persist. Individual tests are not standalone.
- A has `organized/shared_trip/` with the two SHARED_TRIP_PHOTOS (carved out by the runner; seed indexes with rglob in basename order so ids stay stable).
- The peer seed intentionally does NOT set a download directory: the UI can set one but NEVER clear one (empty value rejected), so the no-download-dir scenario must run before any test sets it. Tests set it to `join(tmpdir(),'yaffo_ui_test_downloads')` so node fs can assert on pulled files.

## Environment bugs found and fixed while building this (do not re-investigate)
- p2p env vars go to the FLASK process only (`flaskOnlyEnv` in isolated_runner.ts). The seed script and taskq host also call create_app; with the env they race for the one QUIC UDP port and the web process can lose ("Device sharing is not running").
- Sandbox temp dirs use `realpathSync(tmpdir())`. macOS `/var` → `/private/var` symlink made `granted_media_query` (which prefix-matches `media_dir.path.resolve()` against `full_file_path`) match ZERO files for media-dir/folder grants. Album grants were unaffected (matched by membership) — that asymmetry is the fingerprint of this bug.
- `YAFFO_P2P_EPHEMERAL_IDENTITY=1` (added in yaffo/p2p/identity.py) keeps device keys out of the real macOS keychain.

## Critical selector gotchas
- Chips are `text-transform: uppercase` — `innerText()` returns "SELECT ALL 4 MATCHING". Parse chip text via `textContent()` and collapse whitespace (chips soft-wrap → rendered newlines).
- Toast = `.notification.visible` (single global element). Confirm modal = `#global-confirm-dialog.active` → `#confirm-dialog-confirm`; `data-sharing-confirm` intercepts `htmx:confirm`, so NO native dialog ever fires (nothing to auto-accept).
- `#sharing-sidebar-shared-with-me` is an htmx fragment backed by a live p2p call — reload-poll for rows (see `sharedWithMeView`).
- Grant form selects are hidden searchable-selects (wrapper-click pattern); the folder path is typed straight into `#share-folder-path` after switching share type to Folder. Media-dir option text is `<name> - <path>`.
- Remote-gallery card clicks toggle selection via a capture-phase handler (no navigation). Selection rides the URL: `select_id=…` or `select=all&exclude_id=…`.
- Year facets can match a single file (2008 → 1). The select-all test probes year options until one matches >1 (2014 works).
- Transfers: `#transfers-panel` self-polls every 2s; completion = `.transfer-batch[data-state="completed"]` containing "N of N files". Files land at `<downloadDir>/<peer folder>/<collection>/…`.
- Pairing round-trip is fast on loopback (<2s), but keep generous timeouts (90s per test via beforeEach) — pull batches include peer manifest snapshots.

## Scenario order (dependencies)
1 settings → 2 pair → 3 grant media dir + browse → 4 no-download-dir notice, then SET the dir → 5 pull two → 6 select-all-matching minus one → 7 folder grant (revoked in-test) → 8 album grant (removes one member; membership stays at 3; grant revoked in-test) → 9 revoke the media-dir grant → 10 revoke + delete the device → 11 re-pair, duplicate grant dedupe (revoked in-test).
