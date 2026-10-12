# custom_pages.spec.ts triage

## Failing test
Custom Pages › pages_create_new_page — timed out (30s) at:
`await Promise.all([page.waitForURL(/\/pages\/\d+\/design$/), page.locator('.nav-new-page').click()]);`

## Root cause
- `pages_create` -> 302 -> `/pages/<id>` -> 302 -> `/pages/<id>/design` is correct.
- The pages bar (`#navbar-pages-bar`) visibility comes from `get_show_pages_bar()`:
  returns `setting is not None and setting.value == "true"` — i.e. HIDDEN when no
  `show_pages_bar` ApplicationSettings row exists (fresh DB default).
- base.html renders `hidden` on the bar when `show_pages_bar` is False; nav.js
  `apply(bar.hidden)` keeps it hidden; CSS `.navbar-pages-bar[hidden]{display:none}`.
- The test assumes the bar is "expanded by default" and never expands it, so
  `.nav-new-page` is not actionable and waitForURL never sees navigation.

## Conclusion
test_code_defect. Fix: ensure the bar is expanded before clicking `.nav-new-page`
(e.g. if `#navbar-pages-bar` is hidden or `.nav-new-page` is not visible, click
`#nav-pages-toggle`), and apply the same guard in
`pages_presentation_view_renders_widgets` before clicking `.nav-page-tab`.
