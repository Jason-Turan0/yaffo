# Sharing suite triage (2026-08-23 run: 4/12 pass, 8 fail)

## Root cause: environment timing race (async seed indexing)
- Seed data files exist on disk and media dir is configured; pairing/list_shared work.
- yaffo.db-wal shows seed enqueues async pipeline: import_photos -> index_photos -> classify_labels -> auto_assign_faces.
- classify_labels and auto_assign_faces still RUNNING at 23:00:04 (~4min after test run ended ~22:56:05); index_photos/import_photos COMPLETED.
- Tests browsed the remote gallery ~22:50 before indexing finished -> files list empty -> files.html shows "No shared files found" empty-state (no .remote-photo-grid), folder share 0 cards, /albums has no "Seeded Album" (album built from indexed photos).
- Classification: environment_instability (timing), not test_code_defect and not application_regression (11/12 passed on 08-01 with same app).
- Suggested action: make isolatedEnvironment:start:sharing wait for the seed indexing pipeline to finish (readiness gate) before the suite runs; optionally add a wait in the first browsing test for A's library to be populated.
