# create-customize/automations

## Shots
- `automations-list.webp` (1440x1000): diff of 1.07% was entirely the automations
  sidebar list. `Assign location name` used to be ellipsised by
  `static/components/panel_nav.css` (`.panel-nav-label { white-space: nowrap;
  overflow: hidden; text-overflow: ellipsis; }`); `static/utilities/_base.css`
  overrides it for this panel (`#automations-nav .panel-nav-label { min-width: 0;
  overflow-wrap: anywhere; }` + `{ white-space: normal; }`), so the first label now
  wraps to two lines and pushes the rest of the list down a few px. Benign, expected
  layout change -> promote, no prose change needed. Do not re-triage as
  environment_instability: nowrap+ellipsis cannot turn into a wrap via font noise.
- Shot is scrolled-state free; the fixture is the reviewed `file-favorite-kid-photos`
  automation, disabled, empty run history ("No runs yet — this automation is disabled").

## Prose
- Sidebar prose ("System"/"Custom", green ON badge) holds while labels wrap;
  the guide's bullet list already spells out `Assign location name` in full.
