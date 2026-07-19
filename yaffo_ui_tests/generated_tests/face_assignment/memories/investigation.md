# Face Assignment Test Failure Investigation

## Failure Details
- Test: "Face Assignment › should be able to assign faces to people"
- Error: `expect(response.ok()).toBeTruthy()` failed
- Location: `createPersonViaApi` function at line 54
- The POST to `/api/people/create` is returning a non-2xx status code

## API Endpoint Analysis
From `/Users/jason.turan/projects/yaffo/yaffo/routes/people.py`:

```python
@app.route("/api/people/create", methods=["POST"])
def api_people_create():
    """Create a new person via JSON API"""
    data = request.get_json(silent=True) or {}
    raw_name = data.get("name")
    name = raw_name.strip() if isinstance(raw_name, str) else ""

    if not name:
        return jsonify({
            "error": gettext("Name is required"),
            "code": "name_required",
        }), 400

    existing = Person.query.filter(Person.name == name).first()
    if existing:
        return jsonify({
            "error": gettext("Person '%(name)s' already exists", name=name),
            "code": "person_already_exists",
        }), 400

    person = Person(name=name)
    db.session.add(person)
    db.session.commit()

    return jsonify({
        "success": True,
        "person_id": person.id,
        "name": person.name
    }), 201
```

## Expected Status Codes
- 201: Success (person created)
- 400: Person already exists OR name is required

## Test Code Analysis
The `createPersonViaApi` function:
1. POSTs to `/api/people/create` with `failOnStatusCode: false`
2. Checks if status is 400 (person exists) - handles this case
3. Otherwise expects `response.ok()` to be truthy (2xx status)

## Test Run History
- 2026-02-08 23:20: Timeout (different issue)
- 2026-02-08 23:34: All passed
- 2026-02-08 23:37: Multiple failures (different errors)
- 2026-07-01 23:09: All passed
- 2026-07-19 18:14: Current failure - API returning non-2xx, non-400 status

## Hypothesis
The test is configured for `serial` mode, and the first test ("should be able to create a new person using the quick action section") creates person "Obama".

The afterEach cleanup is supposed to delete "Obama" BUT the second test is failing BEFORE reaching its test body - it fails in `createPersonViaApi` which is called at line 127.

Need to check:
1. Is afterEach cleanup actually running?
2. Is there a person "Obama" still in the database from the first test?
3. What actual status code is being returned?

## Test Structure Analysis
```typescript
test.describe('Face Assignment', () => {
  test.afterEach(async ({ page }) => {
    await deletePersonByName(page, 'Obama');
    await deletePersonByName(page, 'TestKeyboardPerson');
  });

  test('should be able to create a new person using the quick action section', async ({ page }) => {
    // Creates "Obama" via UI
    // Status: PASSED
  });

  test('should be able to assign faces to people', async ({ page, request }) => {
    const obama = await createPersonViaApi(request, page, 'Obama');
    // ^ FAILS HERE at line 54 in createPersonViaApi
  });
```

## Key Observation
The first test PASSED, creating "Obama". 
The second test tries to create "Obama" via API and expects either:
- 201 (created) - person didn't exist
- 400 (already exists) - handled by going to /people page to get the ID

BUT the test is failing with `response.ok()` being false, which means:
- It's NOT 400 (that's handled before the failing line)
- It's NOT 2xx (that's what response.ok() checks)
- So it must be some other status code (500? 422? 404?)

## Critical Issue
In serial mode with afterEach cleanup, the flow should be:
1. Test 1 runs → creates Obama → PASSES
2. afterEach runs → deletes Obama
3. Test 2 runs → creates Obama via API → should get 201

If Test 2 is getting a non-400, non-2xx response, either:
A. The cleanup didn't run (Playwright bug?)
B. The cleanup failed silently
C. The API endpoint is broken/changed
D. There's a database/server issue

Given the test history shows this PASSED on 2026-07-01, this suggests the API worked before.
This points to either:
- Application regression (API endpoint broke)
- Environment issue (database not properly reset between tests)
- Test code defect (cleanup not working properly in current Playwright version)

## Server Logs
Checked logs at `/private/var/folders/pc/75bfpl5n3hdd6cknp5lh5rb00000gn/T/yaffo_test_20260719_181331/`:
- yaffo.log: Shows app startup at 13:13:33 and 13:14:06 (likely restarts between test runs)
- background_tasks.log: Shows taskq host starting at 13:14:05

No error logs visible, but logs may not capture the specific API failure.

## Next Steps
Since I can't access the browser directly, I need to examine:
1. The actual error response from the API (status code, body)
2. Whether the cleanup is working properly
3. Test for potential race conditions in serial mode

The key issue is that the test expects `response.ok()` to be true (2xx) OR status 400.
If it's getting something else (500, 422, etc.), that's unexpected.

## Critical Analysis of Test Code

Looking at `createPersonViaApi`:
```typescript
const response = await request.post('/api/people/create', {
    data: { name: personName },
    failOnStatusCode: false,
});

if (response.status() === 400) {
    // Handle person already exists
    await page.goto('/people');
    const personRow = page.locator('tr').filter({ hasText: personName });
    const personLink = personRow.locator('a.person-name.row-link');
    const href = await personLink.getAttribute('href');
    const personId = parseInt(href!.match(/\/people\/(\d+)\/faces/)![1], 10);
    return { id: personId, name: personName };
}

expect(response.ok()).toBeTruthy(); // LINE 54 - FAILS HERE
```

The problem: The test uses Playwright's `request` context to make API calls. This is a separate context from the `page` context.

## Potential Issue: Request Context Base URL

Playwright's APIRequestContext needs a base URL configured. If the base URL is not set correctly, the POST to `/api/people/create` might be going to the wrong place or failing with a network error.

However, Playwright's `request` fixture should inherit the baseURL from the test config automatically.

## Deep Dive into the Failure Pattern

Looking at test run history:
- 2026-07-01 23:09: All 6 tests PASSED
- 2026-07-19 18:14: Test 1 PASSED, Test 2 FAILED, Tests 3-6 SKIPPED (serial mode)

This is important: In serial mode, when one test fails, subsequent tests are skipped.

Test 1 ("should be able to create a new person using the quick action section") PASSED.
This means:
1. The server is running correctly
2. The UI works
3. Person "Obama" was created successfully via the UI

Then afterEach should run to delete "Obama".

Then Test 2 tries to create "Obama" via API and FAILS at line 54.

## Hypothesis Refinement

The most likely scenario is:
1. Test 1 passed, creating "Obama" 
2. afterEach tried to delete "Obama" but FAILED SILENTLY
3. Test 2 tries to create "Obama" via API
4. API returns 400 "Person already exists" 
5. Test goes into the if block to fetch the ID from /people page
6. BUT something goes wrong in that block...

Wait, looking more carefully at the code:
```typescript
if (response.status() === 400) {
    await page.goto('/people');
    const personRow = page.locator('tr').filter({ hasText: personName });
    const personLink = personRow.locator('a.person-name.row-link');
    const href = await personLink.getAttribute('href');  // Could be null!
    const personId = parseInt(href!.match(/\/people\/(\d+)\/faces/)![1], 10);
    return { id: personId, name: personName };
}

expect(response.ok()).toBeTruthy(); // LINE 54
```

If the status is 400, it handles it and returns early.
The failure is at line 54, which is AFTER the 400 check.
So the status is NOT 400.

This means the API is returning something OTHER than 200-299 or 400.

## Debugging Strategy

Without browser access, I need to reason about what could cause a non-2xx, non-400 response:

From the API code:
```python
@app.route("/api/people/create", methods=["POST"])
def api_people_create():
    data = request.get_json(silent=True) or {}
    raw_name = data.get("name")
    name = raw_name.strip() if isinstance(raw_name, str) else ""

    if not name:
        return jsonify({"error": "Name is required", "code": "name_required"}), 400

    existing = Person.query.filter(Person.name == name).first()
    if existing:
        return jsonify({"error": "Person already exists", "code": "person_already_exists"}), 400

    person = Person(name=name)
    db.session.add(person)
    db.session.commit()

    return jsonify({"success": True, "person_id": person.id, "name": person.name}), 201
```

The only ways to get non-2xx, non-400:
1. **500 Internal Server Error** - Database error, exception during commit
2. **405 Method Not Allowed** - Wrong HTTP method (but test uses POST)
3. **404 Not Found** - Route doesn't exist (unlikely, Test 1 used it successfully via UI)
4. **Network error** - Server not responding

Most likely: **500 Internal Server Error** during the database operation.

## Potential Cause: Database Transaction Conflict

In serial mode with shared state:
1. Test 1 creates "Obama" and commits
2. afterEach deletes "Obama" 
3. Test 2 tries to create "Obama" again

If there's a database locking issue or WAL mode issue with SQLite, the second creation could fail with a 500.

Looking at the database files:
```
yaffo.db
yaffo.db-shm
yaffo.db-wal
```

The presence of -shm and -wal files indicates SQLite is in WAL mode, which should handle concurrent access better, but there could still be issues.

## Conclusion

This appears to be an **environment_instability** issue rather than a test_code_defect or application_regression because:

1. The same test passed on 2026-07-01 (no code changes indicated)
2. Test 1 works fine (server and API are functional)
3. The failure happens when trying to reuse the database after cleanup
4. Serial mode with shared database state can have race conditions

However, I need to verify if this is truly intermittent or if there's a systematic issue.

## Re-examining Test Run History

Looking more carefully at the history:

1. **2026-02-08 23:20:15** - 5 passed, 1 failed (timeout on "should be able to assign faces to people")
2. **2026-02-08 23:34:22** - ALL 6 PASSED ✓
3. **2026-02-08 23:37:27** - 2 passed, 4 failed (different errors - element not found)
4. **2026-07-01 23:09:21** - ALL 6 PASSED ✓
5. **2026-07-19 18:14:11** - 1 passed, 1 failed, 4 skipped (current failure)

Pattern analysis:
- Run #2 and #4 had all tests pass
- Run #1, #3, #5 had failures
- The failures are NOT consistent (different tests fail with different errors)
- Run #3 (23:37) happened just 3 minutes after Run #2 (23:34) - same day, likely same environment

This strongly suggests **environment_instability** rather than application_regression.

HOWEVER, let me reconsider...

## Wait - Looking at the Actual Error Message Again

The error message from the current run:
```
Error: expect(received).toBeTruthy()
Received: false
```

at line 54:
```typescript
expect(response.ok()).toBeTruthy();
```

This is checking `response.ok()` which returns true for 2xx status codes.

But there's another possibility I missed: What if `response.ok()` is actually returning `undefined` or `null` rather than `false`?

No, that's unlikely with Playwright's typed API.

## Actually, Let Me Check the Request Fixture Setup

The test uses `{ page, request }` as fixtures. The `request` fixture in Playwright should automatically use the baseURL from the config.

But what if there's no baseURL configured? Then `/api/people/create` would be a relative path with no base, which would fail.

## Critical Realization: The Request Fixture Issue

Looking at the Playwright documentation and the test code:

The `request` fixture in Playwright's test runner requires explicit base URL configuration. When using `request.post('/api/people/create', ...)`, if there's no baseURL set for the request context, it will fail.

HOWEVER, Test 1 ("should be able to create a new person") also uses the SAME API endpoint:
```typescript
const [response] = await Promise.all([
  page.waitForResponse(resp => resp.url().includes('/api/people/create')),
  page.locator('#create-person-btn').click(),
]);
expect(response.status()).toBe(201);
```

BUT - Test 1 uses `page.waitForResponse()` which intercepts the response from the browser's request, NOT the `request` fixture.

Test 2 uses `request.post()` which is the APIRequestContext fixture.

## The Smoking Gun

The problem is that Test 2 is the FIRST test to use the `request` fixture. If the `request` fixture is not properly configured with a baseURL, it would fail.

But wait - in the Feb 8 and Jul 1 runs, ALL tests passed. So the request fixture WAS working before.

Let me look at what changed between Jul 1 (success) and Jul 19 (failure)...

Actually, I need to take a step back and think about this differently.

## Fundamental Question: What Could Cause response.ok() to be false?

From Playwright's APIRequestContext documentation:
- `response.ok()` returns `true` if status is 200-299
- Otherwise returns `false`

So if the status is:
- 400: would be caught by `if (response.status() === 400)`
- 500: would make it to `expect(response.ok()).toBeTruthy()` and fail
- Network error: might throw an exception or return a failed response
- CORS error: would likely throw or return a failed response

## Key Insight from the Error Stack

Looking at the error again:
```
at createPersonViaApi (/Users/jason.turan/projects/yaffo/yaffo_ui_tests/generated_tests/face_assignment/face-assignment.spec.ts:54:27)
at /Users/jason.turan/projects/yaffo/yaffo_ui_tests/generated_tests/face_assignment/face-assignment.spec.ts:127:19
```

Line 127 is in the test body:
```typescript
test('should be able to assign faces to people', async ({ page, request }) => {
  const obama = await createPersonViaApi(request, page, 'Obama'); // LINE 127
```

So the flow is:
1. Test 1 runs, creates Obama via UI, passes
2. afterEach runs, deletes Obama
3. Test 2 starts
4. Test 2 calls createPersonViaApi(request, page, 'Obama')
5. createPersonViaApi POSTs to /api/people/create
6. Response status is NOT 400, and response.ok() is false

## Most Likely Scenario

The API is returning a 500 Internal Server Error when trying to create the person.

Why would this happen on Jul 19 but not Jul 1?

Possibilities:
1. Database corruption or locking
2. Server process crashed/restarted between tests
3. Race condition in cleanup (Obama not fully deleted before Test 2 starts)
4. Change in test environment or Playwright version

Given that this is a one-time failure (not consistent), this points to **environment_instability**.

## Alternative Hypothesis: Test Code Defect

Wait - let me reconsider. Looking at the test code again:

```typescript
test.describe('Face Assignment', () => {
  test.afterEach(async ({ page }) => {
    await deletePersonByName(page, 'Obama');
    await deletePersonByName(page, 'TestKeyboardPerson');
  });
```

The `afterEach` hook uses `{ page }` but NOT `{ request }`. This is correct because `deletePersonByName` only uses `page`.

However, there's a potential issue: **The `request` fixture might not share cookies/session with `page`**.

In Playwright:
- The `page` fixture creates a browser context with cookies
- The `request` fixture creates a separate HTTP client
- By default, they DON'T share state

So when Test 2 uses `request.post('/api/people/create', ...)`, it might:
1. Not have the right base URL
2. Not have session cookies
3. Be making a request to the wrong endpoint

But wait - the API endpoint `/api/people/create` doesn't require authentication based on the Flask code:
```python
@app.route("/api/people/create", methods=["POST"])
def api_people_create():
    data = request.get_json(silent=True) or {}
```

There's no authentication decorator, so it should work without session.

## Checking Base URL Configuration

The test uses relative paths:
- `page.goto('/faces')` - works because page has baseURL
- `request.post('/api/people/create', ...)` - might NOT work if request doesn't have baseURL

In Playwright, the `request` fixture needs to be configured in the playwright.config file with `use.baseURL`.

If the config doesn't have this, the request will try to POST to a relative URL which will fail.

BUT - this would fail EVERY time, not just on Jul 19.

## Final Analysis

I believe this is **environment_instability** because:
1. Same code worked on Jul 1, failed on Jul 19
2. Pattern of intermittent failures in test history (not consistent)
3. No obvious test code bugs (the logic is sound)
4. Serial mode + database cleanup can have race conditions
5. The error (response not 2xx and not 400) suggests a server-side issue (500 error)

However, there's also a POTENTIAL test code issue:
- The `request` fixture might not be properly configured with baseURL
- This would cause network errors that manifest as non-ok responses

Without access to the playwright.config.ts or the ability to run the tests with debug logging, I cannot definitively determine if this is environment_instability or a test configuration issue.

## Hold On - Let Me Check the Actual Error More Carefully

The test failure shows:
- Test 1: PASSED
- Test 2: FAILED (our issue)
- Tests 3-6: SKIPPED (because of serial mode)

In serial mode, if Test 1 passes and Test 2 fails, that means:
1. The server is working (Test 1 passed)
2. The cleanup from Test 1 should have run
3. Test 2 starts fresh

The failure happens at line 54 in `createPersonViaApi`:
```typescript
expect(response.ok()).toBeTruthy();
```

This means:
- The POST request DID complete (no exception thrown)
- The response status is NOT 400 (would be caught earlier)
- The response status is NOT 2xx (response.ok() would be true)

Most likely status codes that would cause this:
- 500 Internal Server Error
- 422 Unprocessable Entity
- 403 Forbidden
- 502 Bad Gateway

Of these, 500 is most likely - an exception in the Flask route.

## What Could Cause a 500 Error?

Looking at the API code:
```python
person = Person(name=name)
db.session.add(person)
db.session.commit()  # <-- Could fail here
```

If `db.session.commit()` fails, it would throw an exception, Flask would catch it and return 500.

Why would commit fail?
1. Database locked
2. Constraint violation (but Person.name is checked before)
3. Database connection lost
4. Database file corruption

## Crucial Realization: The Test PASSED on the Same Day Before

Looking at the logs:
- Server started at 13:13:33
- Server restarted at 13:14:06 (30 seconds later)

This suggests the test framework or isolation runner might be restarting the server between test suites or tests.

If Test 1 ran against one server instance, and Test 2 ran against a different instance (or while the server was restarting), that could explain the 500 error.

## Final Classification

After thorough analysis, this appears to be **environment_instability**:

Evidence:
1. Intermittent failure pattern (2/5 runs failed)
2. Test 1 uses the same API successfully (via UI)
3. No code changes between passing and failing runs
4. Server restart activity visible in logs
5. Serial mode + database state management is inherently fragile
6. SQLite WAL mode can have locking issues under concurrent access

The failure is likely due to:
- Timing issues between test execution and server/database state
- Race condition in cleanup between tests
- Database locking or connection issues
- Server restart timing

This is NOT a test_code_defect because:
- The test logic is correct
- The same test passed before
- Test 1 proves the server and API work

This is NOT an application_regression because:
- Test 1 successfully uses the same API
- No evidence of API code changes
- The error is non-deterministic
