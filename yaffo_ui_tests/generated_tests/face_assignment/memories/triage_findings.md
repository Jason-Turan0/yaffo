# Face Assignment Test Failure Triage

## Current Situation
- Test run shows 2 passed tests, 4 failed tests
- The database appears to have NO unassigned faces (showing "0 of 0 unassigned faces")
- This means the test data/environment is in a bad state

## Key Finding
When navigating to /faces?group_by=people, the page displays:
- "Showing 0 of 0 unassigned faces"
- "All Faces Assigned!" message
- No .face elements in the DOM
- No .suggestion-group elements in the DOM

## Failed Tests Analysis

### 1. "should be able to assign faces to people"
- Error: `.face[data-face-id="1"]` not found
- Reason: No faces exist to assign (0 unassigned faces in DB)

### 2. "faces are automatically matched to people based on similarity"
- Error: `.suggestion-group` with "Obama" not found
- Reason: No groups created because no unassigned faces exist

### 3. "similar faces are grouped together"
- Error: `.suggestion-group` not found
- Reason: No similarity groups can be created with 0 faces

### 4. "keyboard shortcuts enable quick face assignment"
- Error: `faceIds.length` is 0 (expected > 0)
- Reason: No selected faces because no groups exist

## Test History Pattern
- 2026-02-08T23:34:22.772Z: ALL 6 TESTS PASSED ✅
- 2026-02-08T23:37:27.654Z: 2 PASSED, 4 FAILED ❌
- Previous runs: Same test ("should be able to assign faces to people") was timing out repeatedly

## Database State Verification
Direct checks on the application show:
- 0 unassigned faces in the database
- 0 people in the database
- No Obama person exists
- No TestKeyboardPerson exists

The database is completely empty of test data!

## Conclusion
This is **ENVIRONMENT_INSTABILITY** - The test database has no test data (no unassigned faces, no initial data), likely due to:
1. Test environment setup failed to seed the database
2. Database was cleared/reset but not re-seeded
3. The application is running against an empty database
4. Previous test cleanup might have removed ALL data including base test fixtures

The tests themselves appear correct based on:
- They worked perfectly in a previous run (23:34:22.772Z) - all 6 passed
- The logic is sound (proper selectors, API calls, etc.)
- The failure is consistent with an empty database, not bad test code
- Specification indicates there should be faces (1,11,13,18,26,37,41 for Obama)

This is NOT a test code defect - the tests are written correctly. This is NOT an application regression - the app functions properly with data. This IS environment instability - the test database lacks the required fixture data.

## Supporting Evidence
1. **Test history shows intermittent nature**: One run passes completely (23:34:22), the next fails (23:37:27)
2. **No fixture/seed mechanism found**: No test_data.sql, seed files, or fixture directories in the application
3. **Database initialization only creates schema**: init_db.py only creates tables, doesn't insert test data
4. **Empty database confirmed**: Direct API checks show 0 faces, 0 people
5. **Specification expects data**: According to spec, faces 1,11,13,18,26,37,41 should exist for Obama

The specification indicates "Faces 1,11,13,18,26,37,41 belong to person Obama" but the database has no faces at all. This suggests the test environment needs proper fixture data but isn't getting it consistently.
