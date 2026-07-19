# Albums Reorder Test Failure Investigation

## Test Failure
- **Test**: `albums_reorder_members - Dragging a photo reorders the album and the order persists`
- **Error**: Expected HTTP 204, Received 403
- **Location**: Line 353 in cleanup code
- **Consistency**: Failed 3 times consistently at 2026-07-19T18:01, 18:05, and 18:08

## Analysis

### The Failure Point
The test fails during CLEANUP, not during the actual test:
```typescript
// Cleanup: restore the original order
const body = originalOrder.map((id) => `media_item_id=${id}`).join('&');
const restore = await page.request.post(`${albumPath}/reorder`, {
  data: body,
  headers: { 'content-type': 'application/x-www-form-urlencoded' },
});
expect(restore.status()).toBe(204);  // <-- FAILS HERE with 403
```

### Why 403?
Looking at `/Users/jason.turan/projects/yaffo/yaffo/security.py`:
- CSRF protection is enabled by default
- All POST requests require a valid CSRF token
- Token can be provided via:
  - `X-CSRF-Token` header
  - `csrf_token` form field
- Missing/invalid token returns 403

The cleanup code uses `page.request.post()` which is Playwright's API request context, NOT a browser request. It doesn't carry:
- Browser cookies/session
- CSRF token from the page

### The Main Test Works
The main test dispatches HTML5 drag events which trigger browser-side JavaScript that makes a fetch() request. 

From `/Users/jason.turan/projects/yaffo/yaffo/static/albums/albums.js`:
```javascript
const postOrder = async () => {
    const body = new FormData();
    grid.querySelectorAll('[data-select-id]').forEach((card) => {
        if (card instanceof HTMLElement && card.dataset.selectId) {
            body.append('media_item_id', card.dataset.selectId);
        }
    });
    await fetch(url, { method: 'POST', body });
};
```

From `/Users/jason.turan/projects/yaffo/yaffo/static/security.js`:
```javascript
window.fetch = function secureFetch(input, init = {}) {
    const headers = new Headers(input instanceof Request ? input.headers : undefined);
    new Headers(init.headers || {}).forEach((value, key) => headers.set(key, value));
    headers.set('X-Yaffo-Response', 'json');
    if (window.APP_CONFIG.csrfToken) {
        headers.set('X-CSRF-Token', window.APP_CONFIG.csrfToken);
    }
    return originalFetch(input, { ...init, headers });
};
```

So when the JavaScript calls `fetch()`, it's intercepted by the security wrapper that automatically adds the CSRF token from `window.APP_CONFIG.csrfToken`.

### Repository Code
From `/Users/jason.turan/projects/yaffo/yaffo/db/repositories/album_repository.py`:
```python
def reorder(session: Session, album_id: int, ordered_media_item_ids: list[int]) -> None:
    """Persist manual order (drag-to-reorder). Ids not in the album are ignored;
    members missing from the list keep their relative order after the listed ones."""
    members = {
        row[0]
        for row in session.query(AlbumItem.media_item_id)
        .filter(AlbumItem.album_id == album_id)
        .all()
    }
    position = 0
    for media_item_id in ordered_media_item_ids:
        if media_item_id not in members:
            continue
        session.query(AlbumItem).filter(
            AlbumItem.album_id == album_id, AlbumItem.media_item_id == media_item_id
        ).update({AlbumItem.position: position})
        position += 1
    session.commit()
```

This shows the reorder endpoint itself works correctly when called with proper CSRF token.

## Classification: TEST_CODE_DEFECT

**Reason**: The test's cleanup code uses the wrong API for making authenticated POST requests. It should either:
1. Use browser-based navigation/form submission that carries the CSRF token
2. Extract the CSRF token from the page and include it in the API request
3. Use a different cleanup approach (e.g., drag events to restore order)

The application is functioning correctly - the main test passes, which demonstrates the reorder feature works. Only the cleanup code is broken.

**Affected Test**: `albums_reorder_members`

## Fix Strategy

Use browser-based JavaScript execution via `page.evaluate()` to call the same `fetch()` function that the application's drag-and-drop code uses. This will:
- Automatically include the CSRF token (via the security.js wrapper)
- Use the same code path as the actual application feature
- Be more resilient to future changes in the authentication mechanism

Alternative considered: Extract CSRF token and use in API request headers, but that duplicates the security logic and is more fragile.
