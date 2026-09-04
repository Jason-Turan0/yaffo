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

## 2026-08-30 — responsive rollout (P3: faces and people)

### Status: PASSING (15/15) — 5 pre-existing behaviour tests plus a 10-test `People — responsive` describe

Hand-written, not generated and not healed. Shared assertions come from
`generated_tests/_support/responsive.ts`.

### Layout facts
- `/people` registers **no** panel of its own — it is a plain page with a header
  action. `/people/<id>/faces` registers `#person-faces-actions` and
  `#person-faces-filters` (toggles `#person-faces-actions-toggle` and
  `#person-faces-filters-toggle`), Actions first.
- **Gotcha:** `#nav-menu-toggle` does not carry `data-nav-panel-toggle`, so an
  ordering assertion must select `'[data-nav-panel-toggle], #nav-menu-toggle'`.
- Below 640px `static/responsive.css` (shared, S2-owned) turns `.people-table`
  into labelled cards: `table/thead/tbody/tr/td` all go `display: block`, the
  `thead` is clipped to 1×1 px, and each `td::before` renders `attr(data-label)`.
  Assert the rendered `getComputedStyle(cell, '::before').content`, not just the
  presence of the attribute — the attribute alone does not prove the card reads
  as a labelled record. All six cells must still render; nothing is hidden to
  make the row fit.
- `static/people/list.css` used to be an empty file. It now owns the containment
  the card/table pair needs: `.people-table { overflow-x: auto }` (so the table,
  not the document, scrolls between 640px and desktop), `overflow-wrap: anywhere`
  on `.person-name`, and 44px action links under a coarse pointer.
- The person-faces filters are **range** inputs named `min_similarity` /
  `max_similarity` (ids `min_similarity-range`, `max_similarity-range`).
  `fill('42')` works on them.

### Bug found and fixed (has a scenario naming the cause)
A person's name reaches three places, and all three have to break it: the list
cell, the `.page-header h1` of `/people/<id>/faces`, and — the one that was
missing — that page's empty-state sentence, "No faces have been assigned to
<name> yet." A single unbroken ~90-character name there set the page's minimum
width to **672px at a 320px viewport**. Fixed in `static/people/faces.css`.

### Test-data notes
- `firstPersonId()` prefers the person with the most faces so the gallery, not
  the empty state, is what the coarse-pointer and width tests measure; it falls
  back to the first row when the parallel suites have emptied everyone.
- The long-name fixture (`LONG_NAME`) is created via the API and deleted in a
  `finally`; it is also in `ALL_TEST_NAMES` so `afterAll` sweeps it if the test
  dies mid-flight.
- `.people-table` only exists when at least one person exists — the `{% if %}`
  branch renders `.empty-state` instead. `templates/people/list.html` used to
  close `.main-content` inside the truthy branch only, leaving the element
  unclosed in the empty case; that is fixed.
