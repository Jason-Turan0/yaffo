# create-customize/automations

## automations-list.webp — sidebar label wrapping is expected
- The Automations sidebar deliberately wraps long labels instead of ellipsizing:
  `static/utilities/_base.css` has `#automations-nav .panel-nav-label
  { white-space: normal; overflow-wrap: anywhere; }`, which overrides the
  `text-overflow: ellipsis` rule in `static/components/panel_nav.css`.
- Consequence: "Assign location name" renders on two lines, so every row below it
  in the nav list sits ~1 line lower than in older captures. A diff confined to
  the nav list (~x 60-270, y 413-887 on the 1440x1000 shot) with the top system
  row wrapping is this, not a regression. Newest capture verified as intended and
  promoted; no prose changes needed.
- The guide prose never claims truncation, so this shot has no prose coupling.

## Fixture / walkthrough notes (unchanged)
- Seed fixture: `file-favorite-kid-photos`; the flow creates and deletes a
  throwaway `Documentation Automation …` entry, leaving the fixture clean.
- The flow asserts exactly 7 system automations; that count is unchanged.
