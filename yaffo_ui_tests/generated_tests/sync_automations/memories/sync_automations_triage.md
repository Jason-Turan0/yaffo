# sync_automations triage notes

Failure: immich_validates_server_and_loads_fake_server_collections
- Fails at line 192: expect(process.env.FAKE_IMMICH_URL).toBeTruthy() -> undefined.
- Same 8-test file passed at 2026-10-11T18:01:02Z and earlier runs (6/6, 7/7, 8/8).
- App log from failing run (yaffo.log in /tmp/yaffo_test_20261011_221951_fbc5088a) shows the app correctly rejected the unreachable Immich server (http://127.0.0.1:1) three times (three retries created distinct slugs). App behavior is not regressed.
- FAKE_IMMICH_URL / FAKE_ONEDRIVE_URL are injected by the test runner's fake-provider scripts, not the app.
- Missing env var => fake Immich fixture service was not started/exported for this run.
- The remaining 6 tests were skipped only because the suite runs serially and aborted after the first failure.

Classification: environment_instability (missing test fixture service), not test_code_defect or app regression.
