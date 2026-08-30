# organize-review/labels — notes

- Starter vocabulary is 64 labels (seeded in migration 000 DEFAULT_CLASSIFICATION_LABELS);
  grouped exactly as the page lists them. Count verified stable.
- Classify-labels defaults: confidence_threshold = 50, max_labels = 4 (db/models.py
  CLASSIFY_LABELS_DEFAULT_THRESHOLD / CLASSIFY_LABELS_DEFAULT_MAX).
- Control names match templates: "Photo labels", "Filter labels…", "Add label",
  "Re-classify all photos", "Configure", "Confidence threshold", "Max labels per photo",
  event "Media indexed", match type "Any of these"/"All of these".
- Media detail labels chips show confidence via title tooltip; no direct edit control.
- Root cause checks: global dep changes (app.js, nav.js, selection_bar.js, base.html
  fingerprints) were found NOT to affect the labels page; page prose stayed accurate, so
  no edit was made. Re-verify before assuming a dep change needs a doc change.
