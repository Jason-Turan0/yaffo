# Troubleshooting page notes

- Two shots: index-photos-status.webp (clip `.utility-page` on /utilities/index-photos) and
  ai-generation-status.webp (clip `#llm-section` on /settings).
- Walkthrough selectors verified stable: #stat-orphaned, #scan-results ("Everything is in sync"),
  #media-dirs-list .media-dir-item, #threshold-range (faces), #labels-section, #remove-duplicates-form,
  #map (locations), #llm-section/#llm-model/#llm-api-key.
- Page prose is high-level; verified accurate against templates/routes/automation_config.
  "Sync Database" (index_photos.html), "Media Directories" & "AI Generation" (settings), "Geotag from
  neighbors" & "Reuse a nearby photo's name"/online OSM lookup (automation_config) all present.
- On the 2026-08-30 dependency-only flag (app.js, selection_bar.js, faces/index.js, locations/list.js,
  nav.js, base.html changed, no screenshot diff): page content was already in line; no edits needed.