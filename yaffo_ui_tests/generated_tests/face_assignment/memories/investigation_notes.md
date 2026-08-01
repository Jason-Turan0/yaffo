# Face Assignment Investigation

## Failing Test
- **Test**: "should be able to assign faces to people"
- **Error**: Line 54: `expect(response.ok()).toBeTruthy()` - Received false
- The API call `POST /api/people/create` with `{name: 'Obama'}` returns non-OK, non-400 response

## Key Observations
- First test ("create a new person") passes — it creates Obama via UI
- afterEach deletes Obama via UI, then second test tries to create via API
- The 400 check in createPersonViaApi only handles "already exists" case
- If API returns something other than 200/201/400, it falls through to expect(response.ok())

## Investigation Plan
1. Check the /api/people/create route handler
2. Navigate to the live app and test the API directly
3. Check if Obama already exists before the second test runs
