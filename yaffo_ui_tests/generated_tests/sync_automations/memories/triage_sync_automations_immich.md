# Triage notes: sync_automations Immich failure

- Failing test: immich_validates_server_and_loads_fake_server_collections
- Failure: `process.env.FAKE_IMMICH_URL` is undefined (line 193).
- First portion of the test (invalid server -> "couldn't reach the server") passed.
- App log (/tmp/yaffo_test_20261012_025029_77571ab0/yaffo.log) shows immich sign-in correctly failed "unreachable" for 127.0.0.1:1, proving app validation works.
- No fake_immich.py in app scripts dir (/home/runner/work/yaffo/yaffo/yaffo/scripts only has db/). Fake provider scripts live at repo root scripts/, outside allowed dirs.
- Prior run history: 8/8 passed at 18:01Z on 10-11; latest 8-test run failed only due to missing env var. Serial mode skipped the remaining 6.
- Classification: environment_instability (fake Immich fixture service not started / FAKE_IMMICH_URL not exported), not app or test defect.
