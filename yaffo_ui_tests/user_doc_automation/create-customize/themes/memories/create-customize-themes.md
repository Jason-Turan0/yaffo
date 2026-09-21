# create-customize/themes — notes

- themes-list.webp was recaptured after the responsive/nav commit (fe33512d -> HEAD,
  responsive.css added, nav/panel restructure). The only semantic delta in the shot is
  the DEFAULT badge now rendering at the trailing edge of its nav row, which is the
  shipped fix in `static/themes_page/index.css` (`.theme-nav-default {
  margin-inline-start: auto }` replacing a float that the flex row ignored; the template
  now wraps the label in `.panel-nav-label`).
- Accompanying page-wide pixel delta (~1.46%, bound 2688x1205 at (41,33) on a
  1400x900@2x shot) is sub-pixel text re-rasterisation from the navbar/panel restructure
  in the same commit — every glyph in the navbar, pages bar, sidebar and header is
  edge-flagged, no content, count, colour or state changed. This is not fixture drift,
  so do not quarantine it; the shot is correct.
- Capture state: the default theme at capture time is `classic`, so the badge sits on
  Classic in the System list. The shot setup does not pin the default itself.
- Prose check (still accurate after the recapture): the System list matches THEMES
  (Classic, Darkroom, Neo-Brutalist, Scrapbook, Photos App, Memphis; six entries), the
  Custom section shows the seeded Test Ocean theme, the `New theme` button is present,
  and "The current default is marked in the list" holds (badge visible on the selected
  Classic row).
