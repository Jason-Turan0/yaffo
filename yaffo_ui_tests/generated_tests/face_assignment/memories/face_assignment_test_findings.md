# Face Assignment Test Failures Analysis

## Main Issues Identified:

### 1. Flash Message vs Notification System (PRIMARY ISSUE)
- **Problem**: Tests wait for `.toast.success` but app uses Flask flash messages, NOT client-side notifications
- **Evidence**: 
  - After deletion, DOM shows: `<div class="alert alert-success">` with text "Deleted TestDeletePerson"
  - The `.notification` element is for JavaScript-triggered notifications, NOT form submissions
  - Flask form submissions use server-side flash messages that render as `.alert.alert-success`
  - The flash message appears briefly but disappears before test can check it
- **Impact**: All cleanup in `deletePersonByName()` times out waiting for wrong selector
- **Files affected**: 
  - base.html shows flash messages render as `.alert.alert-{{ category }}`
  - people.py routes use `flash("Deleted {name}", "success")` which renders as `alert alert-success`

### 2. Missing Modal Confirmation Button Selector
- **Problem**: Test uses `#deleteModal button.btn-danger` but the actual button doesn't have `.btn-danger` class
- **Evidence from templates**: The delete modal uses `render_modal()` with "btn-danger" as last param
- **Need to verify**: The actual rendered button selector

### 3. Face Selection State Issue  
- **Problem**: `keyboard shortcuts` test fails because `.face.selected` returns 0 faces
- **Evidence**: Test expects faces in first group to be auto-selected but finds none
- **Root cause**: When grouping by people (default view), faces may not be auto-selected initially

### 4. Face Element Not Found - NEW FINDING
- **Problem**: In "assign faces" test, `.face[data-face-id="1"]` is not found on initial page load
- **Root Cause**: Default view with threshold=10 and group_by=similarity shows NO faces (empty "No results found")
- **Evidence**: 
  - Initial /faces page with default filters: 0 faces displayed
  - After switching to "Group by People" and applying filters: 49 faces displayed, including face 1
- **Why**: High similarity threshold (10) filters out all faces when grouping by similarity
- **Impact**: Test loads /faces and immediately tries to find face 1, but it's not rendered due to default filters

## Fixes Applied:

1. ✅ **Removed flash message waits in cleanup function**
   - Now waits for person row to be removed from DOM instead
   
2. ✅ **Modal button selector was correct** - no changes needed
   
3. ✅ **Fixed keyboard shortcuts test**
   - Now applies filters first to create groups with auto-selected faces
   
4. ✅ **Increased test timeout to 30000ms**

## Remaining Issue:

**Face assignment test fails because default view shows no faces**
- Solution: Apply "Group by People" filter after loading /faces page
- This ensures all faces are displayed before trying to interact with them
- Alternative: Navigate directly to /faces with query params: `/faces?group_by=people`
