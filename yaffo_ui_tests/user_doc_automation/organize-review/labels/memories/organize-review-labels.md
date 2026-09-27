# organize-review/labels — notes

- Page's own memories dir (yaffo_ui_tests/user_doc_automation/organize-review/labels/memories) is empty.
- Shot set: settings-labels.webp, classify-labels-automation.webp, media-labels.webp
  (all present under docs/guide/organize-review/assets/labels/).
- Verified 2024-run: dependency fingerprints for yaffo/static/base.css and
  yaffo/static/sidebar.css changed, but all three shots were pixel-identical and no
  prose claim depends on those files' internals:
    * base.css: adds `[hidden] { display:none !important }`, form-control font/bg
      inheritance, `.page-header-main` basis. Cosmetic hardening only.
    * sidebar.css: scopes `.filter-group > label` to the direct child so nested
      component labels (`.multi-select-option`, `.match-option`, `.radio-label`)
      keep display:flex. Gallery-filter rendering only; no shot of it on this page.
  => intended_change, action promote, no doc edit.
- Verified 2025-run (non-visual dep check, no shot changed): deps changed =
  classify_labels_automation.py, routes/utilities/automations.py, INIT migration,
  run_history.css, en.json, media/view.css, utilities/_base.css, run_history.html,
  media/view.html. Reviewed all: the app gained label *export* (export_photo_tag
  "Export Labels"/"Export Custom Tags" + config), a new `media_labeled` event
  ("Media labeled") emitted by classify_labels + its photo_labeled trigger seeded for
  export_photo_tag, and responsive/run-history CSS. None of it touches this page's
  subject (vocabulary, Classify-labels modal, filter, detail chips) and no prose claim
  depends on those internals. Re-confirmed all page facts still hold (64 labels, 50%,
  4, "Photo labels", "Re-classify all photos", detail-chip confidence tooltip).
  => intended_change, action promote, no doc edit.
- Starter vocabulary = 64 names in DEFAULT_CLASSIFICATION_LABELS
  (scripts/db/migrations/000_MIGRATION_20260620_INIT.py); the page's enumeration
  matches exactly (6+10+12+4+8+7+6+7+4). Defaults 50% / max 4 per photo match
  automation_config.py.
- Walkthrough stubs POST /settings/labels (+ /reclassify) with 204; real reads are
  the gallery filter and media detail. "people swimming" only matches the seeded
  swimming prompt. Detail shot uses 2017-09-12_162200_blowing-candles.png
  (birthday party + cake).
