# Face Assignment Test Generation Progress

## Status
Starting test generation for face_assignment feature

## Test Scenarios Required
1. face_assignment_can_create_new_people - Create person via quick action
2. face_assignment_can_be_done - Assign faces to people
3. faces_are_automatically_matched_to_people_based_on_similarity - Auto-matching faces

## Key Elements Found (from templates)
- Create person input: #create-person-name
- Create person button: #create-person-btn
- Person dropdown: #sidebar-person-select
- Assign button: #sidebar-assign-selected-btn
- Group by radio: #group-by-people, #group-by-similarity
- Threshold slider: #threshold-range
- Apply filters button: Look for in sidebar
- Clear selection: #deselect-all
- Face elements: .face with data-face-id attribute
- Group checkbox: .group-select-checkbox

## Routes
- Face assignment page: /faces
- People list: /people
- Create person API: /api/people/create
- Assign faces API: /api/faces/assign
- Delete person: /people/{id}/delete
- View person faces: /people/{id}/faces

## Key Findings

### Selectors Verified
- Create person input: `#create-person-name`
- Create person button: `#create-person-btn`
- Person dropdown (searchable select): `#sidebar-person-select` (with button wrapper)
- Assign button: `#sidebar-assign-selected-btn`
- Group by People radio: `#group-by-people`
- Group by Similarity radio: `#group-by-similarity`
- Threshold slider: `#threshold-range`
- Apply filters button: button with text "Apply Filters"
- Clear selection link: `#deselect-all`
- Select all link: `#select-all`
- Face elements: `.face` with `data-face-id` attribute
- Group checkboxes: `.group-select-checkbox` with `data-group-index`
- Suggestion groups: `.suggestion-group`
- Suggestion name in heading: Last span in h2 of `.suggestion-group`

### People Page Selectors
- Add person button: button with text "+ Add Person"
- Delete link: link with text "Delete" (in actions column)
- Edit link: link with text "Edit"
- View Faces link: link with text "View Faces"
- Modal person name input: `[name="name"]` inside modal
- Delete modal: `#deleteModal`
- Delete person name display: `#deletePersonName`

### Person Faces Page
- Face cards: `.face-card` with `data-face-id`
- Back to People link: link with text "← Back to People"
- Person name in heading: h1 with pattern "{name}'s Faces"

### JavaScript Behaviors
- Toast notifications appear after actions
- Face assignment uses API: `/api/faces/assign`
- Person creation uses API: `/api/people/create`
- Faces are removed from DOM on successful assignment
- Page reloads after person creation (1.5s delay)
- Modals are used for delete confirmation
- Faces auto-select in first group when "Group by People" is used

## Test Strategy
1. Test 1: Create person via quick action - Check person appears in dropdown after creation
2. Test 2: Assign faces - Verify face removal and check person faces page
3. Test 3: Auto-matching - Check groups and selections after threshold/grouping changes

## Additional Findings

### Notification System
- Element: `#app-notification`
- Classes when visible: `.notification.{type}.visible`
- Types: success, error, warning, info
- Auto-hides after 3 seconds by default

### Face IDs Available
- Face IDs 1-49 are available in the test data
- According to spec: Faces 1,11,13,18,26,37,41 belong to Obama

### Suggestion Group Structure
- Container: `.suggestion-group`
- Heading structure: `h2 > span` (last span contains the person/group name)
- When no person assigned yet: "Unknown" group
- After creating person and assigning: Person-specific groups appear

## Critical Findings for Test Updates

### Searchable Select Behavior
- The select is wrapped in `.searchable-select-wrapper`
- Display button has class `.searchable-select-display`
- Dropdown opens with class `.searchable-select-dropdown`
- Options are in `.searchable-select-option` elements
- Need to click display button first, then click option
- Display text updates to selected option text

### Person Creation Behavior
- Uses API: `/api/people/create`
- Shows success notification after creation
- Page reloads after 1.5 seconds
- Input is `#create-person-name`
- Button is `#create-person-btn`

### Face Assignment Behavior
- Uses API: `/api/faces/assign`
- Faces fade out and are removed from DOM after assignment
- Success notification shows with message pattern: "Successfully assigned X face(s) to PersonName"
- Empty suggestion groups are removed after faces are assigned
- Next suggestion group auto-selects after current group is empty

## Test Updates Required
1. **Person dropdown interaction**: Update to work with searchable-select component
2. **Wait for page reload**: After person creation (1.5s + load time)
3. **Face removal**: Expect faces to be removed from DOM, not just hidden
4. **Person faces page**: Update selector for face elements (.face-card vs .face)
5. **Group structure**: Verify heading text in h2 > span pattern

## Person Faces Page Selectors
- Face card: `.face-card` with `data-face-id`
- Back link: text "← Back to People"
- Person name heading: h1 pattern "{PersonName}'s Faces"

## Status
Ready to generate final updated tests with all correct selectors
