# troubleshooting — run notes

- Dependency-only fingerprint churn (broad CSS/JS/components refactor: base.css,
  icons.css, modal/overlay/tooltip/selection_bar, faces/, filters/filter_config,
  locations/, nav.js, sidebar.css, utilities/_base.*, templates base.html,
  _sidebar.html, faces/index.html, locations/list.html, utilities/_base.html) can
  fire this page's check without any visual change.
- Verified in that state: both captured shots (index-photos-status, ai-generation-status)
  keep their framing and content; every control the prose names still exists —
  Settings → "Media Directories", Utilities → Index Photos → "Sync Database" (hidden
  until a scan finds work, plus "Reindex Library"), stat "Not Indexed", the
  "Geotag from neighbors" system automation, Settings → "AI Generation" model + API key.
- Walkthrough selectors still valid: #stat-orphaned / #scan-results ("Everything is in
  sync"), .utility-page, #llm-model[data-searchable-initialized] + wrapper, #llm-api-key,
  #media-dirs-list .media-dir-item, #threshold-range, #labels-section,
  #remove-duplicates-form, #map.
- So: no prose edit on a dependency-only flag unless a named control/label actually
  changed.
