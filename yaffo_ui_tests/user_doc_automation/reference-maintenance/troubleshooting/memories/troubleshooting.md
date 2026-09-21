# reference-maintenance/troubleshooting

Shots: index-photos-status.webp (clip .utility-page on /utilities/index-photos),
ai-generation-status.webp (clip #llm-section on /settings).

Known-stable facts (checked 2026 run):
- Prose labels still match templates: Settings > "Media Directories",
  "Utilities" sidebar links "Index Photos"/"Remove Duplicates", index-photos
  stats include "Not Indexed" and a hidden-until-work "Sync Database" button,
  Settings > "AI Generation" with "Model" + API key.
- i18n "in sync" empty state text the flow waits on: utilities:indexPhotos.inSync.title
  ("Everything is in sync"); #stat-orphaned / #scan-results ids present.

Dependency-change note: a flagged diff limited to static CSS
(base.css, sidebar.css, locations/list.css — responsive/label-scoping fixes)
produced NO pixel change in either shot and touches nothing the prose names.
Treat style-only dependency drift with identical screenshots as benign
(promote, no doc edit). Walkthrough selectors all still resolve.
