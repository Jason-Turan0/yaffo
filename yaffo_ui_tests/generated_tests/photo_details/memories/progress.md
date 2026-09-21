# Photo Details Tests — Current State (2026-07-01)

## Status: PASSING (5/5) against the isolated sandbox

## Application facts (verified against live app)
- Details page route: `/media/view/{media_item_id}` (renamed from `/photo/view/{id}`). Image binary: `/media/{id}` (was `/photos/{id}`). Face thumbnails: `/faces/{face_id}`.
- Page heading: sidebar `<h2>Photo Details</h2>`. Sections are `.detail-section` blocks with `<h3>` headings that PLURALIZE: "Person (1)" vs "People (2)", "Face (1)" vs "Faces (9)", "Tag/Tags", "Label/Labels".
- Both File Information and Location sections can contain a `Name:` `.detail-item` — always scope lookups to the section.
- Coordinates render with degree symbols: `43.467448°, 11.885127°` — regexes must allow `°`. "View on Map" is `a.action-button` with `target=_blank` to `https://www.google.com/maps?q=<lat>,<lon>` (full precision in the URL, rounded in the display — compare with tolerance).
- People links: `a.person-link` → `/people/{id}/faces` (NOT `.person-tag`, NOT `/person/...`).
- Face hover: `.face-thumbnail[data-face-id]` mouseenter calls `highlightFace()` which draws on `#faceCanvas` and adds `.highlighted`; mouseleave clears. Drawing scales from `#mainPhoto.naturalWidth` and the canvas is sized from the rendered photo — WAIT for the image to be loaded (`img.complete && naturalWidth > 0`) and `canvas.width > 0` before hovering or reading canvas pixels (getImageData throws on a 0×0 canvas).

## Tags editing
- Modal: `#tagsModal`, title `#tagsModalTitle` (NOT `#modalTitle`). Editor list `#tags-editor-list` with `.tag-editor-item` rows (tag name/value live in INPUT values, not text content); add inputs `#modal-new-tag-name` / `#modal-new-tag-value`; add button text "+ Add Tag".
- Saving PUTs the WHOLE tag set to `PUT /api/media/{id}/tags` with `{tags: [{tag_name, tag_value}]}`, then closes the modal and reloads the page (success toast is flashed through sessionStorage).
- There is NO per-tag endpoint anymore (old `POST /api/photo/{id}/tags`, `PUT/DELETE /api/photo/tags/{tag_id}` are gone). Cleanup = snapshot the `.tag-item` name/value pairs before the test, PUT them back after.

## Seeded test data
- Photo 14 = DSCN0010.jpg: has GPS coordinates, has NO faces/people. Used by file-info, location, and tags tests.
- Photo 7 (3 faces) used by the face-hover test; photo 4 (9 faces) used by the people-faces test.

## History of resolved issues (do not re-investigate)
- Coordinate display/URL precision mismatch → compared with 0.01 tolerance.
- Tag rows are inputs, so `hasText` can't find them → use `.last()` after count check.
- UI tag deletion flakiness from the old per-tag API era → moot; the wholesale PUT replaced that API.
- 2026-07: all specs migrated to the /media/view routes and the new tags API.

## Responsive coverage (2026-09-04) — 15/15 passing (5 pre-existing + 10 new)

- New file `photo-details-responsive.spec.ts`; shared assertions come from
  `generated_tests/_support/responsive.ts`. Run the family with
  `npm run test:spec -- generated_tests/photo_details --port <yours>` (a
  directory works — the runner just hands the path to Playwright).
- **Layout below 900px**: `.photo-viewer` stacks, `.photo-container` is ordered
  *above* `.photo-sidebar` (the sidebar is first in source order), the sidebar
  drops `position: sticky`, its max-height and its `.sidebar-content` scroller,
  and the document becomes the only scroller. Assert that with a computed
  `overflow-y` + `scrollHeight > clientHeight` sweep over
  `.photo-viewer/.photo-container/.photo-wrapper/.photo-sidebar/.sidebar-content`.
- `responsive.css` (shared) also carries a `max-width: 768px` block for
  `.photo-*`, and it loads AFTER `media/view.css`, so at ≤768 its values win on
  ties (`.photo-main` 65dvh there, 62dvh from view.css between 768 and 900).
- Viewport-bound heights are `vh` then `dvh` (the fallback pair). The rotation
  test guards that by counting `dvh` rules in the `media/view.css` sheet through
  `document.styleSheets` — same-origin, so `cssRules` is readable.
- **Face highlighting**: `mouseenter` still draws the box, and a `click` now does
  too (`initializeFaceTapHighlighting` in `view.js`), which is the coarse-pointer
  path; the same click also opens the reassign overlay from `face-reassign.js`.
  `updateCanvasSize` redraws the remembered face after a resize, because sizing a
  canvas wipes it.
- GOTCHA: a mouse-driven highlight is cleared by the `mouseleave` that the
  *reflow itself* fires when the thumbnail moves out from under the cursor — so
  any resize/rotation case must run in a touch context (`withTouchContext`) and
  use `tap()`, not `hover()`.
- `.tag-add-row` stacks at ≤640px; `#tagsModal .modal-body` is the only scroll
  region in the dialog.
- Videos for the narrow-player case are found with `/?media-type=video`; the
  detail markup is `video.photo-main`, or `img.photo-main` + `.video-unplayable`
  for a format the browser can't play, or `.media-missing` when the file is gone.
- Touch target sizing: `responsive.css`'s coarse-pointer rule covers `.btn`,
  `.action-button`, `button`, `input`, `select` and friends — but NOT a bare
  `<a>`, which is why `.person-link` needed its own 44px minimum in `view.css`.
- The missing-video state is exercised by temporarily moving one seeded video's
  source inside the disposable environment, reloading its detail route, and
  restoring the source in a `finally` block.
