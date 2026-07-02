# People Tests — Current State (2026-07-02)

## Status
- Generated from `yaffo_ui_tests/specs/people.yaml`.
- Typecheck passes; 5/5 green against the isolated runner
  (`BASE_URL=http://127.0.0.1:5002 npx playwright test generated_tests/people/people.spec.ts`).
- Serial suite; every test uses a unique `SpecTest*-${Date.now()}` person name and
  cleans up via `POST /people/<id>/delete`; `afterAll` sweeps leftovers via the UI.

## People List (/people)
- `.people-table` columns: Name / Gender / Born / Faces / Photos / Actions.
- Row: `a.person-name.row-link` → `/people/<id>/faces`; two `.stat-number` spans
  (index 0 = faces, 1 = photos); `[data-action="edit"]` and `[data-action="delete"]`
  action links with `data-person-id/-name/-birthdate/-gender`.
- Server flash messages render as `.flash-messages .alert-success` (or `.alert-error`):
  "Added X", "Renamed 'A' to 'B'", "Deleted X", "Person updated".
- **Assert flash text locale-independently**: the settings suite briefly switches
  the app language mid-run and flashes render once server-side (a Spanish
  "Persona actualizada" never re-renders, so polling can't recover). Assert the
  interpolated person NAME (stable in every locale) or just `.alert-success`
  visibility for parameterless messages; assert empty states structurally
  (`.face-card` count 0) rather than by English text.
- "No people yet" empty state requires zero people in the DB — not asserted because
  concurrent suites (face_assignment) create people mid-run.

## Add / Edit Modals
- `#addModal` / `#editModal`, opened state = `active` class. Inputs `#addPersonName`,
  `#editPersonName`. Both post to `people_create`/`people_update` as normal forms.
- Gender is a native `<select name="gender">` hidden behind the searchable-select
  widget: click `.searchable-select-display` inside
  `select[name="gender"] + .searchable-select-wrapper`, then click a
  `.searchable-select-option` by text. Values: '' = Not specified, '1' = Male, '0' = Female.
- Birthdate is an intl-date-input: visible `#addPersonBirthdate`/`#editPersonBirthdate`
  takes locale text (en: `MM/DD/YYYY`), parsed on **blur** into the hidden
  `input[name="birthdate"]` (ISO). Assert the hidden value after blur to self-verify.
- `people_create` **ignores** birthdate; only `people_update` persists it. The edit
  test round-trips gender/birthdate by reopening the modal after save.

## Faces Integration
- Setup shortcuts: `POST /api/people/create {name}` → 201 `{person_id}`;
  `POST /api/faces/assign {faces:[id], person:id, faceStatus:'ASSIGNED'}` — runs as a
  background task (taskq host must be up), so poll `/people/<id>/faces` for
  `[data-face-id="<id>"]` (toPass ≤20s).
- Unassigned-pool membership: `/faces?threshold=1` renders `.suggestion-group`
  elements whose `data-faces` attribute is JSON (`[{id, photo_date, similarity}]`),
  including hidden clusters — parse those instead of scraping visible cards.
- **Reserved faces:** ids 1, 11, 13, 18, 26, 37, 41 belong to the face_assignment
  suite's scenarios (person Obama). Never assign them from this suite; both suites
  run in the same parallel session.
- Per-person faces view: `.face-card[data-face-id]` with an `img`; clicking the card
  toggles `.selected` + hidden checkbox; `#remove-selected-faces` → global confirm →
  submits `#remove-form`; redirect lands back with flash "Person updated" and (when
  empty) `.empty-state` "No faces found".
- Deleting a person unassigns their faces (status back to unassigned) — verified by
  polling the pool for the face id.
