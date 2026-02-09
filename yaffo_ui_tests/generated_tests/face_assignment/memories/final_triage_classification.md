# Final Triage Classification

## Classification: ENVIRONMENT_INSTABILITY

## Affected Tests
1. Face Assignment › should be able to assign faces to people
2. Face Assignment › faces are automatically matched to people based on similarity
3. Face Assignment › similar faces are grouped together
4. Face Assignment › keyboard shortcuts enable quick face assignment

## Reasoning

The four failing tests all fail because the test environment database contains NO test data:
- 0 unassigned faces
- 0 people
- No fixture data loaded

### Key Evidence
1. **Temporal Pattern**: Test run at 23:34:22 - ALL 6 TESTS PASSED. Test run at 23:37:27 (3 minutes later) - 4 TESTS FAILED. Same test code, different outcomes.

2. **Database State**: Direct browser verification shows "Showing 0 of 0 unassigned faces" and "All Faces Assigned!" message.

3. **Test Logic is Correct**: The tests that are failing use valid selectors and logic:
   - `.face[data-face-id="1"]` - correct selector per template
   - `.suggestion-group` - correct selector per template  
   - API calls to `/api/faces/assign` - valid endpoint
   - Helper functions properly structured

4. **Tests Passed Previously**: The exact same tests passed completely in a previous run, proving the test code itself is not defective.

5. **Specification Mismatch**: The spec states "Faces 1,11,13,18,26,37,41 belong to person Obama" but the database has ZERO faces total.

6. **No Test Data Seeding**: Review of application code shows no fixture files, no seed data, no test data population mechanism.

### Why Not Test Code Defect?
- Tests passed in a previous run
- Selectors match actual HTML templates
- Test logic follows proper patterns (API setup, UI verification)

### Why Not Application Regression?
- Application renders correctly (shows "All Faces Assigned!" when no faces exist)
- API endpoints respond appropriately
- No errors in application code
- Application works as designed when data exists

### Why Environment Instability?
- Database lacks required test fixtures
- Inconsistent state between test runs
- No reliable data seeding mechanism
- Tests depend on pre-existing data that isn't guaranteed

## Suggested Action

The test environment needs a reliable database seeding mechanism to ensure test data (especially face and photo fixtures) is present before tests run. Options:
1. Add a beforeAll() hook to seed the database with test fixtures
2. Create a database snapshot with test data and restore it before each test suite
3. Implement API calls in test setup to create required test photos/faces
4. Add SQL fixture files that populate test data consistently

The tests themselves are correctly written and should not be modified.
