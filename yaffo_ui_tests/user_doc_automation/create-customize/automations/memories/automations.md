# create-customize/automations

- `assets/automations/automations-list.webp` (1440x1000) legitimately changed: the
  System entry "Assign location name" now wraps to two lines instead of ellipsising
  to "Assign location na…". Cause: `yaffo/static/utilities/_base.css` added
  `#automations-nav .panel-nav-label { white-space: normal; overflow-wrap: anywhere; }`
  (the old truncation lives in `components/panel_nav.css`). Intended change, not
  fixture drift — verify by the wrap, not by pixel count.
- The Automations detail page's run control is labelled **Run…** (`.js-run-files`,
  opens the folder picker in `data-mode="any"`). There is no **Run now** button
  anywhere in `templates/utilities/*automations*`; that name only exists as the
  route/function `automations_run_now`. Guide text that named a "Run now" control
  was wrong.
- The seeded fixture (`file-favorite-kid-photos`) is deliberately disabled with an
  empty run history; `Run…` in the flow is always cancelled on purpose.
