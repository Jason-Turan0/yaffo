# Sync automations UI test notes

- Run this feature with the standard single-instance sandbox. Pairing scenarios belong to `sharing`, not this suite.
- Sync details are `/utilities/automations/<slug>` and render in `templates/utilities/sync_detail.html`.
- The New automation modal exposes Import and Send service radio groups under `[data-sync-services="import"]` and `[data-sync-services="send"]`.
- Built-in Folder setup can be exercised without an account. For a send target, select a folder outside all configured media directories, choose a selection, use Test, enable, and Run now.
- Target output is created inside Playwright's per-test `testInfo.outputPath()` directory so the isolated server can write the copies and Playwright can clean them with the test artifacts.
- Source fixture files are under `yaffo_ui_tests/test_data/photos`; the test imports from that directory into the sync's default `Imports/<name>` library subfolder.
- The sync's folder and album selects use the shared searchable-select widget. Use the visible wrapper's `.searchable-select-display` and `.searchable-select-option` controls.
- The 30-second timeout policy applies to each test. Avoid enlarging it for background runs; investigate slow task-queue behavior instead.
- The AI generator command could not run in this environment because Docker is unavailable. These tests were written by hand and typed with the repository's Playwright TypeScript configuration.
