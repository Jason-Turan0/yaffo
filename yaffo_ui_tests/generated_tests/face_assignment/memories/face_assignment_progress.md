# Face Assignment Test Generation Progress

## Task
Generate Playwright tests for face_assignment feature

## Scenarios to Cover
1. face_assignment_can_create_new_people - Create person via quick action
2. face_assignment_can_be_done - Assign faces to people
3. faces_are_automatically_matched_to_people_based_on_similarity - Auto-matching based on similarity

## Key Selectors Found (from pre-loaded context)
- Create person name input: `#create-person-name`
- Create person button: `#create-person-btn`
- Person dropdown: `#sidebar-person-select`
- Assign Selected button: `#sidebar-assign-selected-btn`
- Clear selection link: `#deselect-all`
- Group by radio buttons: `#group-by-people`, `#group-by-similarity`
- Similarity threshold: `#threshold-range`
- Apply Filters button: `.apply-filters-btn` (from sidebar)
- Face elements: `.face[data-face-id]`
- Group select checkbox: `.group-select-checkbox`

## Routes
- Face assignment page: `/faces`
- People page: `/people`
- Create person API: `/api/people/create`
- Assign faces API: `/api/faces/assign`
- Delete person: `/people/<id>/delete`
- Person faces view: `/people/<id>/faces`

## Status
ALL TESTS PASSING! ✅

## Current Test Results
1. ✅ face_assignment_can_create_new_people - PASSING (1290ms)
2. ✅ face_assignment_can_be_done - PASSING (1680ms)
3. ✅ faces_are_automatically_matched_to_people_based_on_similarity - PASSING (1158ms)

## Fourth Scenario (Adding Now)
4. ⚠️ similar_faces_are_grouped_together - ADDING NOW
   - Group by Similarity (not People)
   - Set threshold to 2
   - Click Apply Filters
   - Verify groups have at least 3 faces (min_samples=3 in DBSCAN)
   - Verify first group is auto-selected

## Key Implementation Details for Scenario 4
- Use #group-by-similarity radio button
- Apply threshold=2
- Click filter button
- Check .suggestion-group elements
- Each group should have >= 3 .face elements
- First group's faces should have .selected class
- First group checkbox should be checked

## Key Findings from Live Testing (2026-01-18)
- Creating person via quick action works and updates the UI immediately
- Person appears in dropdown AND keyboard shortcuts
- Flash message appears: "Deleted TestPerson" (on people page)
- Toast/notification for creation not visible in DOM snapshot (may be temporary)
- After person is created, they appear in Person filter dropdown
- Suggestion groups have structure: `.suggestion-group` with heading showing person name
- Group checkboxes auto-select first group's faces
- When grouping by People with threshold=2, faces appear in groups (Obama or Unknown)
- Face elements on assignment page: `.face[data-face-id]`
- Face elements on person view page: `.face-card`

## Critical Selectors Verified
- Create person input: `#create-person-name`
- Create person button: `#create-person-btn`
- Person dropdown (searchable): Uses `.searchable-select-display` and `.searchable-select-option`
- Group by People radio: `#group-by-people`
- Threshold slider: `#threshold-range`
- Apply Filters button: `button.btn.btn-primary.filter-btn`
- Clear selection: `#deselect-all`
- Group checkbox: `.group-select-checkbox`
- Suggestion group: `.suggestion-group`
- Suggestion group heading contains person name
- Assign Selected button: `#sidebar-assign-selected-btn`
