# Labels Tests — Current State (2026-07-02)

## Status
- Generated from `yaffo_ui_tests/specs/labels.yaml`.
- TypeScript validation passes with `cd yaffo_ui_tests && npm run typecheck`.
- Playwright runtime passes against the isolated runner: `BASE_URL=http://127.0.0.1:5002 npx playwright test generated_tests/labels/labels.spec.ts` -> 7 passed on 2026-07-02T01:54:27Z.

## Settings Labels Panel
- `/settings` contains `#labels-section`.
- Vocabulary chips are `.label-chip`; display name is `.label-chip-name`; enable checkbox is `.label-chip-input`; delete button is `.label-chip-remove`.
- `#label-filter` is a delegated client-side filter from `static/settings/labels.js`. It matches label names and prompt tooltip text from `.label-chip-info[data-tooltip]`.
- Filtering sets `hidden` on non-matching chips; `.label-filter-empty` is visible only when a non-empty query has no matches.
- Create form is `.add-label-form` with `input[name="name"]`, `input[name="prompt"]`, and submit button. Create/delete post to `/settings/labels` and swap `#labels-section`.
- Toggle posts to `/settings/labels`, returns `204`, and does not swap the section. The checkbox state is the UI feedback.
- Toggle tests should click `.label-chip-toggle`; directly mutating `.label-chip-input` with `setChecked()` does not reliably fire the app's HTMX post.
- Duplicate label create returns a toast (`.notification.visible`) with status `204` and no swap, so typed input remains in the form.
- Re-classify button is `.labels-reclassify button`, posts `/settings/labels/reclassify`, swaps none, and shows a toast.

## Automations
- Classify labels is the system automation at `/utilities/automations/classify_labels`.
- System automations have no `#edit-automation-button` or `#delete-automation-button`.
- Configure button is `#configure-automation-button`; modal is `#configureAutomationModal`.
- Config fields:
  - `#config-confidence_threshold`
  - `#config-max_labels`
- Trigger rows are rendered from `templates/utilities/automations_triggers.html`; the classify-labels event trigger is `media_indexed` / "Media indexed" when seeded in the environment. Some isolated runs show the edit-triggers link without trigger rows, so tests should treat rows as optional and still assert the link is present.
- Run history is `#automation-runs` and rows are `.automation-run-row`. The fragment self-polls while runs are unfinished.

## Gallery / Details
- Gallery label filter uses a `.multi-select-wrapper` containing `input[name="labels"]` options.
- The match type is a hidden radio group, not a select: `#labels-match-type input[value="any|all"]`. It becomes visible only after at least two labels are selected.
- Apply filters through `#filter-form button[type="submit"]`; selected labels appear in the query string as `labels=...` and `labels-match-type=...`.
- Filter option labels use `data-label` for display text, but the submitted `labels` query parameters are numeric option values.
- Photo cards are `.photo-card`; the media view path is embedded in `onclick="window.open('/media/view/{id}', '_blank')"`.
- Media detail labels render inside a `.detail-section` with an `h3` containing `Label` or `Labels`. If labels exist, chips are `.labels-chips .label-chip` with title text like `Confidence: ...`; otherwise the section contains `.no-data` text `No labels`.

## Test Data / Stability Notes
- The seeded vocabulary should include common defaults such as `dog`; tests assert more than 10 chips rather than exactly ~64 to tolerate vocabulary changes.
- CLIP classifications depend on local model assets and sample images. Tests do not assume a specific photo has a specific label.
- The suite is serial because it mutates shared label vocabulary and classify-labels automation config.
- Custom label cleanup removes `ui-label-*` through the UI if it remains after a test.
