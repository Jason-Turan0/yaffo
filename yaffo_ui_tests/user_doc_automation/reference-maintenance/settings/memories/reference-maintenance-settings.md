# reference-maintenance/settings

## Instance: non-visual dependency check (no screenshot change)
- Flagged deps (base.css, components/{file_browser,icons,modal,overlay,selection_bar,tooltip}.css,
  selection_bar.js, nav.js, notification.css, sidebar.css, base.html) are the
  responsive-navigation/layout refactor. Verified by diffing the page lock before/after:
  only those files changed; settings/index.html and static/locales/en.json hashes identical.
- The Settings page renders identically (shot sha256 unchanged), so the page's prose and
  walkthrough still hold. Action: promote, no doc change.

## Traps seen
- `reference-maintenance/settings/settings.json` is a STALE reference artifact: it proposes
  renaming "Application language" -> "Interface language" and dropping the walkthrough's
  ignoreRegions. Both are wrong for the current code: templates/settings/index.html renders
  `_("Application language")` and en.json maps settings.applicationLanguage to the same, and
  the ignoreRegions (.media-dir-path, #current-thumbnail-dir, #thumbnail-size) are still
  needed because those values vary by host. Do not adopt it.
- Other pages' {page}.json equal the committed files; settings' does not, which is how the
  stale one was spotted.
