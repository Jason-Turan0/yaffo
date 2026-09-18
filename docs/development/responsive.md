# Responsive website plan

Status: **In progress — P1–P4 and P6–P8 complete; P5 acceptance review underway; cross-cutting Phase 5 hardening and the milestone integration gate remain**

Last updated: **2026-09-17**

This plan covers every server-rendered Yaffo page, shared component, built-in
theme, supported locale, and client-side interaction. The goal is one adaptive
interface rather than separate desktop and mobile implementations.

## Outcomes

The work is complete when:

- every page works without page-level horizontal scrolling at a 320 CSS pixel
  viewport; intentionally wide content such as a data table may scroll inside a
  clearly bounded container;
- primary content, navigation, filters, forms, actions, dialogs, and transient
  UI remain reachable with touch and mouse at every supported width;
- layouts respond to the space available to the component instead of assuming
  a particular device;
- resizing or rotating the viewport does not lose selections, entered values,
  scroll position, open media, map state, or in-progress work;
- the interface remains usable at 200% text zoom and with long translated text;
- left-to-right and right-to-left layouts work without maintaining separate
  markup;
- the classic, darkroom, memphis, neobrutalist, photos-app, and scrapbook themes
  preserve their visual identity at every supported width;
- every page family's Playwright suite asserts that page's own responsive
  behaviour — viewports, panel contract, state across resize, coarse-pointer
  paths, scroll ownership — not merely that its route loads; and
- automated responsive checks prevent new overflow and interaction regressions.

Accessibility — keyboard operation, focus management, ARIA semantics, reading
order, screen-reader behaviour — is deliberately **out of scope here** and is
planned separately in `docs/development/accessibility.md`. The two overlap at
the edges (a control that is off-screen is a problem for everyone), but they
want different evidence: responsive work is judged by geometry at a viewport,
accessibility by a rule engine and assistive-technology behaviour. Mixing them
made both harder to finish.

## Current implementation status

The responsive foundation is now running in the application, but the rollout is
not complete. The shell and Home page are the reference implementation. Other
page families have smoke coverage and several targeted adaptations, but they
have not all received a complete interaction, locale, and theme review.

| Area | Status | Evidence and remaining gap |
| --- | --- | --- |
| Guardrails | **In progress** | Responsive coverage is defined as scenarios inside each page family's `yaffo_ui_tests/specs/*.yaml`, with shared assertions extracted to `generated_tests/_support/responsive.ts` (overflow diagnostics, viewport fit, panel contract, touch drag). Runnable coverage is committed and passing for P1–P4 and P6; P5 now has runnable responsive scenarios, and P7–P8's are implemented and passing in isolation. Every page family therefore has runnable responsive coverage. Representative visual baselines, long-locale fixtures, and the complete cross-theme matrix remain. |
| Application shell | **Implemented** | Mobile Menu is mutually exclusive with page actions, hidden on desktop, closed on first paint, and preserves the Pages behavior. Escape ownership between a panel and a dialog opened inside it, safe-area insets, and on-screen-keyboard handling are in. Visual review across all themes/locales remains. |
| Page panels | **Contract frozen; every sidebar migrated** | The Home pilot was approved for app-wide rollout on 2026-08-30. All eleven page sidebars now register through `components/nav_panel.html`, the legacy `.responsive-panel-toggle` initializer and `static/responsive.js` are deleted, and the applied-filter count badge is server-rendered. Page owners refine *content* inside their panels; the contract itself is S2-owned. |
| Home header and pagination | **Implemented** | Grid/Timeline is vertically centered. Pagination keeps text on desktop and uses one inline row of themed 44 px icon controls on mobile. Next-page navigation no longer flashes the menu. |
| Shared components | **Hardened** | Narrow layouts exist for common containers, tables, modals, forms, actions, and pagination. Notifications, overlays, tooltips and dropdowns are clamped to the viewport and use logical properties; the selection bar clears the sticky navbar; viewport-bound surfaces use dynamic units and safe-area insets. Per-component visual review across themes remains. |
| Core workflows | **P1–P4 and P6 complete** | Library/timeline, media detail, albums, faces, people, and locations have page-family responsive implementations and interaction-level Playwright coverage. Sharing and the remote gallery are complete under P6, including their German/Arabic pass. Utilities and automations have P5 interaction coverage, with visual/locale hardening still open. P7 (settings, themes, standalone screens) and P8 (custom pages and widgets) are implemented and covered, each green in isolation. |
| Validation | **Every page-family suite passing; milestone gate pending** | On 2026-09-17, isolated suites passed Settings 17/17, Themes 13/13, and Custom Pages 18/18. Frontend checks pass: `npx vitest run` 198/198 (22 files), `npm run typecheck:js`, both ESLint configs, `npm run validate:specs` 14/14, and `python -m pytest tests/` 1817 passed / 3 skipped including the design-token drift guard. Earlier gates: P5 on 2026-09-14 (Index Photos 5/5, Remove Duplicates 8/8, Automations 15/15), P1–P4 115/115 on 2026-09-04, P6 on 2026-09-15; those suites were not rerun here. The complete Playwright suite in one run remains the milestone integration gate. |

Current milestone estimate: the shared foundation and every page-family task
except P5's acceptance review are complete. What remains is the cross-cutting
Phase 5 matrix — all themes, the full locale sweep, 200% text zoom, narrow-width
profiling — and the integration gate that runs everything in one pass. Passing
route containment is only a smoke signal; each closed task is closed by its
interaction, touch, resize-state, long-content, and scroll-ownership coverage.

### P5 audit — 2026-09-14

The September 10 implementation already added responsive coverage for Index
Photos, Remove Duplicates, and Automations. The plan now distinguishes that
implementation from the remaining acceptance review.

Findings from this continuation:

| Classification | Finding | Resolution and evidence |
| --- | --- | --- |
| Broken visual order / clipped content | Arabic editor screenshots showed source-code operators reordered by inherited RTL direction, with line beginnings outside the initial code viewport. | Source code uses isolated LTR direction and left alignment. The automation locale scenario checks language/direction, code-line visibility, action containment, and unsent input across all six viewports. |
| Unreachable interaction during scan | Adding a blank duplicate-directory row scanned the application's working directory because `Path("")` becomes `Path(".")`. The audit response counted 737 unrelated media files and delayed the input appearing. | Reject blank paths before constructing `Path`; Python and Playwright regressions keep the initial media count at zero. |

The automation locale scenario attaches minimum-width and desktop editor
screenshots for English, German, and Arabic to the isolated Playwright report
under `yaffo_ui_tests/reports/automations__automations/`. These are review
artifacts, not committed visual baselines.

Classic-theme editor screenshots were reviewed at 320 and 1440 px in all three
locales, including before/after Arabic code-panel captures. P5 remains open for
all-theme visual review, 200% text zoom, the utility and trigger-editor locale
matrix, mixed-direction conversation prose, and run-history review. P7–P8 and the full
milestone integration gate remain separate work.

### P6 audit — 2026-09-15

| Classification | Finding | Resolution and evidence |
| --- | --- | --- |
| Unreachable interaction | Every remote file's name, folder, date, location and size lived in a `.photo-hover` overlay, so a touch user could not read a shared file's metadata at all — and tapping the card selected it instead. | The overlay is a `<details>` disclosure carrying `data-selection-ignore`, so opening metadata no longer selects the file. `remote_gallery_touch_metadata_selection_pagination_and_pull` opens it in a coarse-pointer context and asserts both. |
| Viewport overflow | At 320 px the grant form's native `<select>` sized itself to its longest option — a media-directory option carries a whole filesystem path — pushing the document to 888 px. It only showed before `searchable-select.js` hydrated the control away, so it never appeared in a test that did any work first. | `min-width: 0` on the grant/pairing cards (they are grid items, so `min-width: auto` was blocking the clamp) and `max-inline-size: 100%` on the select. `sharing_routes_fit_every_contract_width` restores the pre-hydration state deterministically instead of racing the script. |
| Broken visual order | Filesystem paths inherited the page direction, so under Arabic a leading `/` — a neutral bidi character — was reordered to the visual end and the path read back to front. | Paths use `unicode-bidi: plaintext`, taking direction from their own first strong character, so an Arabic folder name still reads correctly. `sharing_translated_surfaces_fit_and_keep_path_reading_order` measures where a remote filename's first and last characters actually paint. |

**A test-infrastructure trap worth knowing.** `page.screenshot({ fullPage: true })`
permanently tears down Chromium's mobile emulation on a context created with
`isMobile: true`. After one fullPage capture, `(pointer: coarse)` and
`(hover: none)` stop matching for the rest of that context, so every
coarse-pointer rule silently stops applying — measured against the real
application, a sharing button was 44 px before the capture and 37 px after, with
no application change in between. Attach viewport screenshots inside a touch
context, or take the fullPage one as the test's last action. This cost a full
debugging cycle here and will cost the same in P7 and P8; a `touchScreenshot`
helper in `generated_tests/_support/responsive.ts` is a **shared-owner request**.

**Second shared-owner request.** `[data-tooltip]` reveals only on `:hover` and
`:focus-visible`, and `:focus-visible` does not match a touch tap — so the
`.help-tip` affordance has no coarse-pointer path. This is not sharing-specific
(settings and faces use it too), so it belongs to the shared component owner
rather than to a page task.

### P7 and P8 audit — 2026-09-17

Both remaining page families are complete: implementation, scenarios, runnable
Playwright coverage, and a green isolated run each (Settings 17/17, Themes 13/13,
Custom Pages 18/18).

**P7 — settings, themes, and standalone screens.** Implemented in
`static/settings/index.css`, `static/themes_page/index.css`,
`templates/themes_page/index.html`, `static/error.css`, and the two shell-light
templates; covered by `specs/settings.yaml` and `specs/themes.yaml` and their
generated tests. Every fix in the table below was re-checked by reverting it and
confirming the matching test fails — a scenario that cannot fail is not coverage.

| Classification | Finding | Resolution |
| --- | --- | --- |
| Clipped content | `static/responsive.css` hides `[data-tooltip]::before` below 640 px to drop the JS tooltip's arrow. On a `.help-tip` that `::before` **is** the icon glyph, so every label prompt and English-only marker collapsed to a 0×0 invisible control on phones. | The glyph is restored for settings' help tips. The shared rule is too broad and is a **shared-owner request**. |
| Viewport overflow | The shared pure-CSS tooltip is a 260 px box centred on a 15 px anchor. Label chips wrap right up to the section's inline end, so the bubble hung 48 px past the document edge and settings scrolled sideways with nothing hovered. | The bubble is pinned to the viewport at every width, not only below 640 px. |
| Undersized target | A chip's prompt marker, its remove control, and the searchable-select display box are all below 44 px on a coarse pointer — the shared rule sizes `button, input, select` height only. | Scoped minimums for the icon-only controls; the searchable-select fix is page-scoped and reported upstream. |
| Undersized target | The CSRF and demo-disabled shells load their own stylesheets and never linked `button.css`, so their single action rendered as a bare 18 px link with no fill, padding, or touch target. | Both shells now link `button.css`; `error.css` makes `.error-action` a flex box so the label centres inside the reserved 44 px. |
| Broken visual order | `.theme-nav-default` used `float: right` inside a flex row, which ignores floats — the badge never moved. | `margin-inline-start: auto`, which also follows the writing direction in RTL. |
| Viewport overflow | The themes nav put a raw custom-theme label straight into the `<a>`, skipping the shared `.panel-nav-label` span, so one long unbreakable name ran past the 250 px column and out of the mobile panel. | The label uses the shared span and ellipsises, as albums, utilities and sharing already did. |

**P8 — custom pages and widgets.** Implemented in `static/pages/grid.js`,
`static/pages/detail.css`, `site_agents/widget_templates.py`, and the widget
prompt; covered by `specs/custom_pages.yaml` and its generated tests, plus
`tests_js/pages/grid.test.js`. The first full run of that suite found one more
defect, listed last below.

| Classification | Finding | Resolution |
| --- | --- | --- |
| Lost state | Saving from a narrow canvas persisted the *reflowed* layout: the grid's live nodes are the one-column layout, so a save from a phone flattened every widget to one column and wrote back the heights the reflow had inflated. | The authored 12-column geometry is read from the server-rendered `gs-*` attributes **before** GridStack initializes and kept in a map; that map, not the grid, is what Save writes. |
| Clipped content | The vendored `gridstack.min.css` carries width/offset rules only for `.gs-12` and `.gs-1` — upstream's `gridstack-extra.css` (2–11) is not vendored — so every `.gs-6` item collapsed to 0 px in the intermediate band while its header buttons still painted over the canvas. | `pages/detail.css` supplies the missing six-column rules. |
| Unreachable interaction | The direct move/resize controls were revealed on `:focus-within` only, which a mouse never produces on its own, leaving them unreachable on desktop. | Revealed on hover as well as focus. |
| Unreachable interaction | Design-mode drag gestures competed with page scrolling on touch. | GridStack move/resize is disabled in that mode, so a swipe scrolls the page and the direct controls are the touch path. |
| Unreachable interaction | **Found 2026-09-17.** Move down silently did nothing after any resize. The canvas floats, so shrinking a widget leaves the freed row behind as a hole; GridStack's `engine.swap()` refuses two items that are neither the same size nor touching, and the refusal was treated as "no move". On a phone the direct controls are the *only* way to reorder, so the one path that exists stopped working after the other control was used. | One column is compacted after a resize (it is a stack, not a canvas), and a refused swap now exchanges the two positions inside a batch instead of giving up. Covered at both levels: `custom_page_widgets_move_and_resize_without_dragging` asserts the freed row closes and that Move down still reorders straight after a resize, and a `tests_js/pages/grid.test.js` case drives the refused-swap branch directly. |

### Visual review findings — 2026-09-17

The first pass of `docs/development/responsive-visual-checklist.md` — the manual
sweep over what geometry assertions cannot judge. Eight screens were signed off
unchanged; four were not, and all four are fixed and covered.

| Classification | Finding | Resolution and evidence |
| --- | --- | --- |
| Broken visual order (P7) | The label prompt was pinned to the bottom of the viewport at every width. That fixed the overflow, but on a 1440 px screen it stranded the text a long way from the 15 px marker it explains. | Split by width: `components/tooltip.js` anchors a clamped, flipping popover above 640 px; the CSS bar stays below it. Reveal is split by input — hover, `:focus-visible`, or a press on a coarse pointer. `settings_label_prompt_tooltips_open_as_an_anchored_popover` and `settings_label_prompt_popover_opens_on_a_tablet_tap`. |
| Broken visual order (P7) | The media-directory row stacked on a phone, dropping Remove onto its own full-width line — it read as a second, page-wide action, directly under the path it deletes. | The row is out of the shared stack group and stays a row: the path wraps, Remove keeps its own size beside it. |
| Unreachable interaction (P4) | Tapping a cluster on a phone or tablet also opened the preview photo in a new tab. OpenLayers emits the map's click from the touch's `pointerdown`, so the panel was on screen before the browser synthesized that tap's mouse events, and they landed on the panel rather than the map — usually on the preview photo, whose link carries `target="_blank"`. | `locations/list.js` swallows that one ghost click, scoped to the tap's own position and a 400 ms window, so a deliberate press still goes through. `locations_tapping_a_cluster_does_not_activate_the_panel_underneath`. |
| Clipped content (shared) | In the folder picker the Create-folder button spilled out of its form and painted over the folder list. `modal.css` sets `.modal-content form { flex-direction: column }` for the modal's own stacked forms, and that selector outranks a component's own rule — so the create row became a column whose `flex: 1` input resolved a zero basis against the height, leaving the form measured at the children's natural heights while the coarse-pointer rule stretched each to 44 px. | `file_browser.css` states the direction where it can win and wraps the row instead when the controls do not fit. The broad `modal.css` rule is left alone but is a **shared-owner note**: any row-layout form placed in a modal meets it. |

The canvas policy is deliberately **container-driven, not viewport-driven**: the
design canvas shares its row with a 360 px editor panel until 900 px, so at a
1280 px window the design canvas is in the six-column band while the
presentation view — same window, no editor panel — is still on twelve. A
viewport media query cannot tell those apart. Bands are 12 columns at ≥ 900 px,
6 at ≥ 600 px, and 1 below that, each with a minimum row floor so a widget that
re-wraps when it goes full-bleed does not become a nested scroll region.
Generated widgets are now required to be internally responsive by the widget
system prompt, and the built-in templates wrap their toolbars and clamp their
fixed-width controls.

**A second test-infrastructure trap, alongside P6's fullPage/mobile-emulation
one.** The canvas carries `grid-stack-animate`, so a band change *transitions*
every item's box. The column class and the `gs-*` attributes land immediately
while the geometry is still travelling, so a measurement taken the moment the
class appears reads a number somewhere between the old layout and the new one —
two of these scenarios failed on geometry that was in fact correct. Watching for
two frames that round to the same value is not enough either; it passes
mid-transition. `settleCanvas` in the custom-pages spec waits two rAFs, so the
style change is committed and the transitions exist, then awaits each animation's
`finished`. Any suite that measures an animated layout needs the same shape, so
this is a **shared-owner request** alongside P6's `touchScreenshot`.

One measurement convention is worth recording because it reads backwards: the
grid's 8 px margin insets an item's *content*, not the item. A full-bleed widget
in the single-column band is therefore exactly the canvas wide, and it is
`.grid-stack-item-content` that measures canvas − 16.

## The shared panel contract

Frozen on 2026-08-30 (gate S1), after the product owner approved the Home pilot
for app-wide rollout. This is the app's **one** narrow-screen navigation model;
the older generic `.responsive-panel-toggle` initializer and `responsive.js`
were deleted rather than left to compete with it.

**Registration.** A panel is any element carrying `data-nav-panel` and an `id`.
Its peer button is rendered by `templates/components/nav_panel.html` into
base.html's `nav_context_toggles` block, carries `data-nav-panel-toggle` and
`aria-controls="<panel id>"`, and is server-rendered so the closed narrow state
is already correct on first paint. `_sidebar.html` takes a `panel_prefix` and
registers `<prefix>-actions` and `<prefix>-filters` as two separate panels.

**Ordering.** Toggles read in declaration order and **Menu always sorts last**.
A page with both Actions and Filters declares Actions first: it acts on the
current selection, so it is the likelier destination once items are picked.

- On narrow screens, page-specific actions such as **Filters** are peers of
  **Menu** in the main navbar. Do not put page panels inside the Menu panel and
  do not create nested collapse panels.
- A page may register multiple peer actions. Only one of Menu or any page panel
  may be open at a time. `aria-expanded` carries that state (the accessibility
  workstream owns whether the ARIA semantics are complete); Escape and outside
  click close the active surface.
- **Escape belongs to the topmost surface.** `nav.js` ignores Escape while a
  `.modal.active` is open, so a dialog opened from inside a panel is not
  dismissed together with its host.
- Reuse the existing panel DOM. Move it into the navbar host at the responsive
  breakpoint and restore it to a marker on desktop so entered values, selected
  options, and component state survive resize. Panels are resolved by id on
  every use, and an htmx swap that replaces one re-parks it.
- A closed panel is `hidden`, never merely collapsed to zero size, so it takes
  no layout space and cannot be interacted with by accident.
- **Applied-filter counts** are server-rendered by the `applied_filter_count`
  Jinja global, so the badge never pops in after hydration. A multi-valued
  filter counts once; pagination, view, sort, and scope keys do not count.
  Compact Clear/Apply affordances were considered and deliberately left out:
  the filter form's own Apply/Clear stay where they are.
- Closed mobile UI must be correct in CSS before JavaScript initializes. This
  prevents Menu or panel content from flashing during full-page navigation.
- A mobile filter panel renders its contents without desktop `.sidebar`
  background, padding, radius, or shadow. Avoid empty framing and nested scroll
  regions.
- Menu and page-action buttons use an 8 px gap, at least a 44 px target, a clear
  theme-token active state, and theme-aware icons following
  `docs/development/icons.md` (shared outline mask plus neobrutalist override).
  At 400 px and below, their visible text is hidden so Actions, Filters, and
  Menu fit on one navbar row; the 44 px targets and explicit accessible names
  remain, and an applied-filter badge is positioned inside its button.
- Shared pagination retains localized text labels on desktop and renders
  First/Previous/Next/Last icons at 640 px and below. All controls stay on one
  row at the 320 px minimum width.
- Drag interactions need a real touch path and a direct-control alternative
  where practical. Touch reordering uses Pointer Events, captures the pointer on
  a stable ancestor rather than the moving row, reserves a 44 px handle, and is
  tested with Chrome's real emulated touch stream rather than only synthetic DOM
  events.
- Keep structural responsive CSS in the shared or owning page/component
  stylesheet. Themes skin states with tokens and icon art; they do not define a
  separate responsive layout.
- Viewport-bound surfaces use `dvh` with a `vh` line before it as the fallback.
  The document declares `viewport-fit=cover` and
  `interactive-widget=resizes-content`, so use `env(safe-area-inset-*)` on
  anything flush to an edge and expect the on-screen keyboard to shrink the
  layout viewport rather than scroll over it.

## Current-state audit and remaining risks

The original audit found isolated narrow-screen rules without a site-wide
contract. The branch now has a shared responsive layer, adaptive navigation,
and route-level containment coverage. The table is retained as the rollout
inventory; its risk column now describes what remains rather than the original
starting state.

| Area | Current risk | Primary files |
| --- | --- | --- |
| Application shell | The narrow Menu and page-panel host are implemented, with Escape ownership and safe areas. Remaining work is long locales and visual verification of every theme decoration. | `yaffo/templates/base.html`, `yaffo/static/base.css`, `yaffo/static/pages/nav.css`, `yaffo/static/nav.js`, `yaffo/static/responsive.css` |
| Shared layout and controls | The responsive layer covers common containment and touch targets, every page registers its panels through the one contract, and overlays, pickers, notifications, and viewport edge cases are handled. Per-theme visual review remains. | `yaffo/templates/_sidebar.html`, `yaffo/templates/components/nav_panel.html`, `yaffo/templates/components/`, `yaffo/static/sidebar.css`, `yaffo/static/form.css`, `yaffo/static/table.css`, `yaffo/static/button.css`, `yaffo/static/components/` |
| Library and albums | P1/P2 complete: the library has a touch-safe timeline jump path, rotation-safe streaming, coarse-pointer card behavior, and responsive pagination; albums cover every route, selection, filters, dialogs, long titles, and direct touch-sized reorder controls. The remote gallery is complete under P6. | `yaffo/templates/index.html`, `yaffo/templates/_timeline_sections.html`, `yaffo/templates/albums/`, `yaffo/static/index.css`, `yaffo/static/albums/albums.css`, `yaffo/static/media/` |
| Media detail | P1 complete: portrait/landscape stacking, dynamic viewport units, document-owned scrolling, touch face highlighting and redraw, metadata actions, tag editing, video playback, and missing-video states are covered. | `yaffo/templates/media/view.html`, `yaffo/static/media/view.css`, `yaffo/static/media/view.js` |
| Faces and people | P3 complete: source previews use a viewport-fixed centered modal on phones and anchored popovers on tablets/desktops, Actions/Filters remain separate panels, and assignment state, shortcut reordering, cluster pagination, dialogs, people cards, person galleries, and long names are covered. | `yaffo/templates/faces/`, `yaffo/templates/people/`, `yaffo/static/faces/index.css`, `yaffo/static/people/` |
| Locations | P4 complete: the existing selection DOM becomes a centered assignment modal below 900 px, every hover-only fact has a touch path, OpenLayers is resized after layout changes, and map, selection, and unsaved assignment state survive rotation and breakpoint changes. | `yaffo/templates/locations/list.html`, `yaffo/static/locations/list.css`, `yaffo/static/locations/list.js` |
| Utilities | Navigation uses peer panels; stats/results, duplicate review, automation actions, code, and trigger editors have responsive coverage. Cross-theme review, 200% text zoom, and full utility/trigger locale coverage remain. | `yaffo/templates/utilities/`, `yaffo/static/utilities/` |
| Sharing | P6 complete: the sidebar and remote filters are peer panels, pairing/device/grant forms keep unsaved values across resize, remote metadata has a tappable disclosure instead of a hover overlay, transfers and long device/path values stay in document flow, and codes and filesystem paths keep their own reading order in German and Arabic. | `yaffo/templates/sharing/`, `yaffo/static/sharing/sharing.css`, `yaffo/static/sharing/` |
| Settings and themes | P7 complete: long paths and API-key status wrap, the API-key row is a real wrapping action row, label help tips are visible and tappable with a viewport-pinned bubble, theme nav names ellipsise, draft/publish actions wrap, and the built-in themes are asserted contained at 320 and 390. Covered by Settings 17/17 and Themes 13/13. | `yaffo/templates/settings/`, `yaffo/templates/themes_page/`, `yaffo/static/settings/index.css`, `yaffo/static/themes_page/index.css` |
| Custom pages | P8 complete: container-driven column bands with row floors, an authored-layout record that keeps Save honest from a narrow canvas, direct controls on hover and focus that keep working after a resize, design-mode drag disabled on touch, presentation reflow in source order, and internally responsive generated widgets. Covered by Custom Pages 18/18. | `yaffo/templates/pages/`, `yaffo/static/pages/detail.css`, `yaffo/static/pages/grid.js` |
| Error and demo states | P7 complete: the error card wraps translated copy, uses `dvh`, and trims its padding on phones; the CSRF and demo shells link `button.css` so their one action is a real 44 px target on a coarse pointer. Covered by `settings_standalone_screens_fit_and_keep_their_action_reachable`. | `yaffo/templates/404.html`, `yaffo/templates/500.html`, `yaffo/templates/db_error.html`, `yaffo/templates/security/`, `yaffo/templates/demo/`, `yaffo/static/error.css`, `yaffo/static/demo-mode.css` |
| Themes and localization | Theme skins override structural selectors, while German, Hindi, and Arabic expose wrapping and direction assumptions that English does not. | `yaffo/static/themes/`, `yaffo/static/locales/`, `yaffo/translations/` |

For every unfinished row, capture representative desktop and narrow screenshots
before changing it and log each failure as one of: viewport overflow, clipped
content, unreachable interaction, undersized target, broken visual order, or
lost state. Route smoke tests alone do not replace this workflow audit.

## Support contract

Use content-driven breakpoints, with 640 px and 900 px as the initial shared
boundaries because existing components already use them. A page may introduce a
different boundary only when its content demonstrates the need. Keep layout
rules in the owning shared or page stylesheet; theme stylesheets should skin the
result, not define a parallel responsive layout.

Exercise at least these viewport classes during development:

| Viewport | Purpose |
| --- | --- |
| 320 × 568 | Minimum-width and short-height stress case |
| 390 × 844 | Typical narrow portrait layout |
| 844 × 390 | Narrow landscape and constrained-height behavior |
| 768 × 1024 | Tablet portrait and intermediate wrapping |
| 1024 × 768 | Tablet landscape and desktop transition |
| 1440 × 900 | Existing desktop behavior and regression baseline |

The contract also includes coarse-pointer use, 200% text zoom, reduced motion,
safe-area insets, and both `dir="ltr"` and `dir="rtl"`. Keyboard-only operation
belongs to `docs/development/accessibility.md`.
Prefer logical CSS properties such as `margin-inline-start` when touching
directional layout. Use `dvh`/`svh` with a safe fallback for viewport-bound
panels so mobile browser chrome does not hide controls.

## Architectural work

### 1. Establish shared responsive primitives

- Define shared container gutters, readable content widths, stack gaps, and
  responsive grid minimums in the global layout layer. Use `min-width: 0` on
  flex/grid children that contain user text, paths, tables, or media.
- Add reusable stack/split behavior for the page header, action groups, forms,
  card grids, and sidebar/content shells. Page styles should opt into these
  primitives instead of duplicating media queries.
- Keep breakpoints in CSS. JavaScript should react to semantic media queries
  only when behavior truly changes, and CSS should remain responsible for
  presentation.
- Replace physical left/right declarations with logical properties where they
  affect flow, while retaining physical coordinates where they describe media
  overlays or map geometry.
- Add a development-only overflow diagnostic or Playwright helper that reports
  the element extending the document width, not just the fact that overflow
  exists.

### 2. Make the application navigation adaptive

- **Implemented:** `base.html` has a mobile Menu; Home adds Filters
  through the page-action block. The top-level destinations, Pages controls,
  active destination, custom-page actions, and Pages preference remain intact.
- **Implemented:** `nav.js` enforces mutual exclusion, moves/restores live panel
  DOM, manages `aria-expanded`, Escape/outside dismissal, breakpoint changes,
  and the published navbar-height variables.
- **Implemented:** safe-area insets and Escape ownership between a panel and a
  dialog opened from inside it. Body scroll is
  deliberately *not* locked while a panel is open: the panel host is inside the
  sticky navbar and contains its own overscroll, so the page behind it may
  scroll without the panel moving.
- **Remaining:** visual review of every theme and locale. Keep the wide
  navigation visually unchanged.

### 3. Create one narrow-screen page-panel pattern

**Done — see "The shared panel contract".** Retained here as the rationale:

- Use Home as the reference: page sections are labeled peer buttons beside
  Menu, not nested disclosures inside the page or Menu. A page may expose more
  than one section, but only one page panel or Menu is visible at a time.
- Keep the wide sticky sidebar presentation and move the same live DOM into the
  shared navbar panel host on narrow screens. Preserve form/component state
  across open, close, resize, and rotation.
- A page with Actions plus Filters gets one peer button each, Actions first.
  Applied-filter counts are settled (a server-rendered badge); compact
  Clear/Apply affordances were considered and rejected for this milestone.
- Support Escape and outside dismissal. Evaluate browser history/back only if a
  panel becomes a sheet or otherwise represents navigation state.
- Recalculate sticky offsets from the measured navbar height. Avoid nested page
  scroll regions; mobile page panels should drop desktop sidebar framing.

### 4. Harden shared components

- Page headers and action bars: wrap predictably, keep the title readable, and
  make high-priority actions full-width only when the available space requires
  it.
- Forms: stack `.form-row` labels and controls, let paths and code wrap or scroll
  within their field, and keep validation messages adjacent to the input.
- Tables: wrap every truly tabular surface in a horizontal scroller with a
  visible affordance. Convert the people list and other
  row-action-heavy tables to labeled cards at narrow widths when comparing
  columns is not the primary task. Never hide data solely to make a row fit.
- Modals and pickers: use edge gutters on tablets and a full-height sheet on
  narrow screens; keep `.modal-body` as the only scroll region, wrap footer
  actions, and account for the on-screen keyboard.
- Popovers, searchable selects, multi-selects, tooltips, and notifications:
  clamp to the visual viewport, flip placement when needed, give hover-only
  content a coarse-pointer path, and allow long localized content to wrap.
- Pagination, selection bars, job cards, chat, file browser, date/distance
  inputs, and cron builder: verify wrapping, scroll ownership, and minimum touch
  targets as shared components before page-specific work.
- Apply reduced-motion preferences to layout transitions, drawers, modals,
  overlays, card hover motion, and loading animations.

## Page-family rollout

Implement and review each phase as a complete vertical slice: markup, CSS,
behavior, localization, theme compatibility, and Playwright coverage ship
together.

### Phase 0: Baseline and guardrails

1. **Done:** responsive scenarios are written into each page family's own
   `yaffo_ui_tests/specs/*.yaml`, against the seeded application. There is no
   standalone responsive feature — see "Verification strategy".
2. **Done:** overflow diagnostics, viewport-fit, the peer-panel contract, real
   touch drag, and coarse-pointer contexts are reusable helpers in
   `generated_tests/_support/responsive.ts`. Runnable page-family code is present
   for P1–P6; P7–P8 still have to implement their scenarios.
3. **Remaining:** capture baselines for every page family in classic English,
   then stress the shared shell with German and Arabic and the most structurally
   divergent built-in themes.
4. **Remaining:** turn the support contract above into a review checklist so
   new responsive failures are not accepted as known debt during the rollout.

### Phase 1: Shell and shared components

1. **Done:** adaptive primary/custom-page navigation.
2. **Done:** the peer page-panel contract replaced the generic nested
   sidebar/disclosure rollout, and every page family's sidebar is migrated.
3. **Done:** global containers, page headers, action groups, forms, tables,
   pagination, modal layout, and touch targets, plus the notification, overlay,
   search/multi-select, selection bar, chat, file/folder picker, and
   on-screen-keyboard audits. Job-progress cards inherit the shared container
   rules and had no narrow-width defects.
4. **Done:** the standalone CSRF and demo-disabled shells now load the shared
   responsive layer and declare `viewport-fit=cover`; the error/database-error
   screens already extend base.html and are covered by the width matrix.

The shared contract and a page-family change must not be edited concurrently by
different agents. The contract is now frozen, so page owners consume it.

### Phase 2: Core photo workflows

1. **Library grid and timeline — done (P1):** Home filters, grid containment,
   view switch, pagination, favorite/video touch containment, the touch-safe
   timeline jump control, streamed state across rotation, and sticky landing
   offsets are implemented and covered.
2. **Albums — done (P2):** overview, detail/edit, add-photo filters, selection,
   cover/share dialogs, long content, resize-state preservation, and explicit
   44 px move controls are implemented and covered. HTML drag remains the mouse
   path; direct controls are the touch-safe alternative.
3. **Media detail — done (P1):** portrait and landscape stacking, dynamic
   viewport units, document-owned scrolling, faces/people/tags/favorites,
   touch face highlighting, metadata actions, video playback, and missing-video
   states are implemented and covered.
4. **Remote gallery — done (P6):** the grid, filter panel and pagination reuse
   the completed library behavior; previews, the download-directory notice, touch
   selection, metadata disclosure and pulls across pagination are covered.

### Phase 3: Organization and administration

1. **Faces — done (P3):** the grid is contained; coarse pointers can open source
   previews; Actions and Filters are separate peers; assignment state, cluster
   pagination, shortcut reordering, selection, and dialogs are covered.
2. **People — done (P3):** the six-column list becomes labeled mobile cards;
   add/edit dialogs, person face galleries, filters, resize-state preservation,
   coarse-pointer controls, and long names are covered.
3. **Settings — done (P7):** long filesystem paths and API-key status wrap, the
   stacked directory row keeps Remove a deliberate inline-end target rather than
   a full-width bar, the API-key actions are a wrapping flex row, and label help
   tips are visible, tappable, and no longer push the page sideways. The folder
   picker, unsaved input across resize, German and Arabic copy, and the
   standalone error and CSRF shells are covered.
4. **Utilities — implemented; acceptance review in progress (P5):** index-photo
   stats/results and duplicate review have runnable containment, touch, panel,
   pagination, and resize-state tests. Blank duplicate-directory rows no longer
   scan the working directory. Theme, zoom, and locale review remain.
5. **Themes — done (P7):** navigation is migrated to the peer-panel contract and
   ellipsises long custom names through the shared `.panel-nav-label`; the default
   badge uses a logical margin instead of a float that a flex row ignored; theme
   and draft actions wrap; the page uses `dvh`. The peer-panel contract, all six
   built-in theme pages at 320 and 390, draft actions on a coarse pointer, and the
   generation chat's own scrolling and unsent text across resize are covered.

### Phase 4: Spatial and authoring workflows

1. **Locations — done (P4):** the map remains the primary narrow-screen surface
   and the existing selection panel becomes a centered, backdrop modal.
   Hover-only information has a coarse-pointer path, OpenLayers receives size
   updates after layout transitions, and center, zoom, selection, and unsaved
   assignment state survive resize and rotation.
2. **Automations — implemented; acceptance review in progress (P5):** editor/chat
   stacking, trigger input and touch save, contained code/test tables, long names,
   and dialogs have runnable tests. English, German, and Arabic editor checks
   preserve unsent input across all six viewports; source code retains LTR
   direction inside RTL layouts. Full theme/zoom and trigger-locale review remain.
3. **Custom pages — done (P8):** the canvas runs 12/6/1 column bands chosen from
   the *canvas* width rather than the window, each with a minimum row floor; the
   authored 12-column geometry is captured before GridStack initializes so a save
   from a narrow canvas still writes the desktop layout; design-mode drag is
   disabled on touch with the direct controls as the touch path, and those
   controls are reachable by mouse hover as well as focus and keep working after a
   resize; presentation reflows in (grid_y, grid_x) source order; widget iframes
   get their real container box; and generated widget HTML is required to be
   internally responsive by the widget system prompt.
4. **Sharing — done (P6):** pairing QR/code, device and grant forms, file pulls,
   transfer status, and long device/path content are implemented and covered,
   with a German/Arabic pass over the pairing and grant surfaces.

### Phase 5: Cross-cutting hardening

All Phase 5 items remain milestone exit work:

1. Run every responsive smoke case across all built-in themes. Remove structural
   theme overrides or add narrowly scoped compatibility rules where a decorative
   effect changes geometry.
2. Run the matrix in English, German, and Arabic, then spot-check the remaining
   supported locales. Fix truncation, bidirectional text, and logical alignment
   rather than shortening translations.
3. Test 200% text zoom, coarse pointer, reduced motion, portrait/landscape
   rotation, and short viewport heights.
4. Profile image-heavy grids, maps, and widget pages at narrow widths. Responsive
   work must not cause duplicate media downloads, layout thrashing, or expensive
   resize loops; debounce only work that measurement shows is costly.
5. Update developer documentation with the final sidebar, navigation,
   breakpoint, table, and testing conventions so future pages inherit them.

## Parallel execution plan

### Shared gates and ownership

**All four shared gates are closed as of 2026-08-30.** The page-family work
below can now be split across independent agents. What each gate settled:

1. **S0 — Product decision (shared gate): APPROVED.** The product owner reviewed
   the Home pilot and approved peer navbar panels for the rest of the app, with
   two riders: the legacy generic initializer is retired as part of S1 rather
   than page by page, and the Filters button gets an applied-filter **count
   badge only** (no sticky Clear/Apply footer).
2. **S1 — Shared contract: FROZEN.** See "The shared panel contract" above for
   registration, Actions-plus-Filters ordering, active state, DOM restoration,
   panel scrolling, filter counts, Escape ownership, and breakpoint behaviour. `static/responsive.js` and every
   `.responsive-panel-toggle` rule are deleted, so there is exactly one mobile
   navigation system. All eleven sidebars are migrated: Home, locations, albums
   bulk-add, faces, person faces, and the remote gallery as filter panels; the
   albums, utilities, automations, sharing, and themes navigations as nav
   panels.
3. **S2 — Shared component hardening: DONE for this milestone.** The shared
   owner's files are listed below. Landed: logical, clamped, safe-area-aware
   notifications; overlays and tooltips clamped to the viewport; dropdown
   heights bounded on short viewports; the selection bar offset below the sticky
   navbar; `dvh` fallbacks and `interactive-widget=resizes-content` for the
   on-screen keyboard; the shell-light CSRF and demo screens brought under the
   shared responsive layer; and an `actions` icon registered in both the shared
   outline set and the neobrutalist override. Reduced motion was already covered
   globally.
4. **S3 — Shared test infrastructure: DONE.** Overflow, viewport-fit,
   panel-contract, touch-drag, and touch-context helpers are
   extracted to `generated_tests/_support/responsive.ts`. Responsive scenarios
   are written into each page family's own spec, so no two page agents edit the
   same file. P1–P6 have runnable implementations; P5 acceptance review and
   P7–P8 implementations remain outstanding.

**Ownership going forward.** These files stay with the shared owner; a page
agent reports a need against them rather than patching them from a page task:
`yaffo/templates/base.html`, `yaffo/templates/_sidebar.html`,
`yaffo/templates/components/nav_panel.html`, `yaffo/static/nav.js`,
`yaffo/static/responsive.css`, `yaffo/static/base.css`,
`yaffo/static/components/` (modal, overlay, tooltip, notification, selection
bar, file browser), icon registration, `yaffo/static/types/global.d.ts`,
and `yaffo_ui_tests/generated_tests/_support/responsive.ts`.

### Independent page-family tasks

The shared gates are closed, so every unfinished row below can be assigned now.
Each owner has its page templates, page-specific CSS and JavaScript, fixtures,
responsive scenarios in `yaffo_ui_tests/specs/*.yaml`, and corresponding
Playwright code. Page-local rules stay in the owning stylesheet; edits to the
shared file set above go through the shared owner. Every page's panel is already
registered, and every family now has runnable responsive coverage that passes in
isolation. P5's remaining acceptance review is the only open page-family work.

| Task | Independent scope and acceptance target | Primary ownership | Shared dependency |
| --- | --- | --- | --- |
| **P1 — Library, timeline, and media detail — COMPLETE** | Grid/timeline behavior, scrubber alternative, rotation/state preservation, loading, video and missing-video states, metadata, faces/tags, and portrait/landscape behavior are implemented. Current isolated suites: gallery 27/27 and media detail 15/15. | `yaffo/templates/index.html`, `yaffo/templates/_timeline_sections.html`, `yaffo/templates/media/`, `yaffo/static/index.css`, `yaffo/static/media/` | Panels registered. Shared pagination/modal changes landed through integration. Specs: `specs/photo_gallery.yaml`, `specs/photo_details.yaml`. |
| **P2 — Albums — COMPLETE** | Overview, detail/edit, add-photo filters, selection, cover/share dialogs, long content, and mouse/touch-safe reorder paths pass at the contract viewports. Current isolated suite: 19/19. | `yaffo/templates/albums/`, `yaffo/static/albums/` | Panels registered (`albums-nav`, `album-add-filters`). Shared header and touch-target fixes landed through integration. Spec: `specs/albums.yaml`. |
| **P3 — Faces and people — COMPLETE** | Assignment actions, phone-modal/tablet-and-desktop-popover source previews, shortcut reordering, selection, dialogs, person galleries, filters, long names, pointer parity, and the three-control 375 px navbar are covered. Current isolated suites: faces 20 scenarios and people 15/15. | `yaffo/templates/faces/`, `yaffo/templates/people/`, `yaffo/static/faces/`, `yaffo/static/people/` | Panels registered (`faces-actions`/`faces-filters`, `person-faces-*`). Specs: `specs/face_assignment.yaml`, `specs/people.yaml`. |
| **P4 — Locations — COMPLETE** | The narrow map plus centered assignment modal, coarse-pointer equivalents, reliable OpenLayers resizing, and center/zoom/selection/unsaved-state preservation are covered. Current isolated suite: 21/21. | `yaffo/templates/locations/`, `yaffo/static/locations/` | Panel registered (`locations-filters`). The narrow presentation reuses the existing selection DOM as a viewport-contained modal. Spec: `specs/locations.yaml`. |
| **P5 — Utilities and automations — IN PROGRESS** | Responsive implementations and interaction tests are present for all three suites. Editor locale/RTL coverage is added; complete theme, zoom, utility/trigger locale, and run-history review before closing P5. | `yaffo/templates/utilities/`, `yaffo/static/utilities/`, automation templates/styles/scripts | Panels registered (`utilities-nav`, `automations-nav`). Code/table/chat primitives come from the shared owner. Specs: `specs/index_photos.yaml`, `specs/remove_duplicates.yaml`, `specs/automations.yaml`. |
| **P6 — Sharing and remote gallery — COMPLETE** | Pairing and QR/code, device and grant forms, remote filters and previews, touch metadata, file pulls, transfers, pagination, long device/path content, and a German/Arabic direction pass are implemented and covered. | `yaffo/templates/sharing/`, `yaffo/static/sharing/` | Panels registered (`sharing-sidebar`, `remote-files-filters`). Reuses the final library behaviour from P1. Spec: `specs/sharing.yaml`. |
| **P7 — Settings, themes, and standalone states — COMPLETE** | Paths, API keys, label chips and their help tips, destructive actions, theme nav/draft/chat, and the error/security/demo screens are adapted and covered, including long-copy cases, the German/Arabic pass on settings, and all six built-in theme pages at 320 and 390. Current isolated suites: settings 17/17 and themes 13/13. | `yaffo/templates/settings/`, `yaffo/templates/themes_page/`, standalone templates, `yaffo/static/settings/`, `yaffo/static/themes_page/`, `yaffo/static/error.css`, `yaffo/static/demo-mode.css` | Panel registered (`themes-nav`). Shared file-browser/modal/chat issues go to the shared owner. Do not modify theme skins except for page-specific verified compatibility fixes. Specs: `specs/settings.yaml`, `specs/themes.yaml`. |
| **P8 — Custom pages and widgets — COMPLETE** | Container-driven column bands and row floors, the authored-layout record that keeps Save correct from a narrow canvas, the design-mode gesture policy, direct controls that survive a resize, source-order presentation reflow, iframe sizing, and generated-widget responsiveness are all in and covered, with `tests_js/pages/grid.test.js` covering the band logic and the refused-swap reorder. Current isolated suite: 18/18. | `yaffo/templates/pages/`, `yaffo/static/pages/detail.css`, `yaffo/static/pages/grid.js`, widget templates/runtime | Shared direct-control/icon patterns come from the shared owner; otherwise independent. Spec: `specs/custom_pages.yaml`. |

P1–P8 should each ship as a vertical slice: implementation, **updated
Playwright coverage of that page's responsive behaviour**, desktop regression,
coarse-pointer checks, relevant locale/theme review, and before/after
screenshots. An agent must not mark a task complete merely because its route
passes the overflow smoke matrix — see "Test coverage — required, not optional"
in the definition of done for exactly what its tests have to assert.

#### Acceptance criteria for a page-family agent

This is the single gate for P1–P8. An agent runs **only its own section** — it
never has to green the whole app to finish its task. Running everything is the
integration owner's job, after the parallel tasks merge.

1. **Extend the family's spec.** Add the responsive scenarios the page needs to
   `yaffo_ui_tests/specs/<feature>.yaml` (a starting set is already there).
   Inspect the spec afterwards and confirm each new scenario names the behaviour
   it is asserting, not just the route it visits.
2. **The family's Playwright spec passes in isolation**, against a clean
   environment:

   ```
   npm run seed:build                                              # once
   npm run test:spec -- generated_tests/<feature>/<feature>.spec.ts
   ```

   `test:spec` takes the **generated spec path**, not the YAML. Build the seed
   cache once and let runs restore from it; `--fresh` re-runs the whole
   indexing, face-detection and labelling pipeline on every invocation and is
   not the normal path.
3. **The unit tests pass**, for whatever the change actually touched:
   - `npx vitest run` (from the repo root) whenever any `yaffo/static/**`
     JavaScript changed — the Playwright suite does not cover these;
   - `npm run typecheck:js` and `npm run lint` (repo root) for the same;
   - `python -m pytest tests/` for template, route, or Python changes;
   - `python -m pytest tests/yaffo/test_design_tokens.py` whenever any CSS
     changed — it is the drift guard that rejects raw colours in stylesheets;
   - `npx tsc --noEmit`, `npm run lint`, and `npm run validate:specs` from
     `yaffo_ui_tests` for spec and helper changes.
4. **No shared file is edited from a page task.** Changes to the shared set
   listed under "Ownership going forward" go through the shared owner.

### Shared integration and milestone exit

After the parallel page tasks merge, one integration owner performs work that
cannot be safely partitioned. This is the **only** role that runs the whole
suite — a page agent is gated on its own section (see "Acceptance criteria for a
page-family agent"), because eight agents each greening the whole app is both
wasteful and a source of cross-suite state collisions:

1. resolve shared CSS cascade and breakpoint conflicts introduced by combined
   page work;
2. run the complete Playwright suite — every family's responsive scenarios
   generated, committed and passing, none left outstanding — plus `npx vitest
   run`, `npm run typecheck:js`, both ESLint configs, `python -m pytest tests/`,
   the design-token drift guard, and `npm run validate:specs`;
3. verify cross-page shell behavior, navbar height, browser back/forward
   behavior where applicable, and no first-paint flashes; and
4. update this document's status table and conventions after shared behavior is
   proven, then remove obsolete responsive paths rather than retaining two
   systems.

## Verification strategy

Keep the existing desktop Chromium project as the behavior regression suite.
Add focused responsive coverage rather than replaying every destructive or
long-running end-to-end scenario at every viewport.

Responsive coverage is written the same way as every other suite here: as
**scenarios in the owning page family's `yaffo_ui_tests/specs/*.yaml`**, with
runnable Playwright code committed under the corresponding `generated_tests/`
directory. There is no standalone "responsive" feature — a page's narrow-screen
behaviour is part of that page's spec, which is also what keeps the P1–P8 tasks
independent. Assertions that
belong to the *contract* rather than to one page live in
`generated_tests/_support/responsive.ts` (overflow diagnostics, viewport fit,
the peer-panel contract, real touch drag) and are imported by the generated
tests. The shell contract itself is exercised on Home, so it is
specified in `specs/photo_gallery.yaml`.

Automated checks should include:

- a route-level smoke matrix that loads every page family at 320, 390, 768,
  1024, and 1440 px widths and fails on page-level overflow or uncaught errors;
- navigation, Pages menu, sidebar/sheet, modal, popover, and pagination behavior
  at wide and narrow widths;
- resize-through-breakpoint tests with populated forms, selected photos/faces,
  an open location panel, and an edited custom-page layout;
- representative visual snapshots for the shell, gallery, detail viewer, table,
  map, automation editor, and page builder;
- long-content fixtures for translated labels, filenames, paths, device names,
  person names, album titles, and custom page/widget titles; and
- coarse-pointer equivalents for hover-only affordances, and touch-safe
  alternatives to drag gestures.

While implementing a phase, run **that family's spec only** —
`npm run test:spec -- generated_tests/<feature>/<feature>.spec.ts` — plus
whichever unit-test commands the change touches. The full `yaffo_ui_tests`
suite and the complete frontend checks are the integration owner's gate at
milestone exit, not a per-agent one. Visual review remains required for
OpenLayers, GridStack, media/video fitting, theme decoration,
on-screen-keyboard behavior, and RTL because geometry assertions alone cannot
establish usability.

Accessibility checks are **not** part of this suite. They are planned in
`docs/development/accessibility.md` around a rule engine, which gives
deterministic, attributable findings instead of the hand-written focus
expectations this plan used to carry.

## Definition of done for each page

- No page-level horizontal overflow from 320 px through desktop widths.
- No clipped, overlapped, or unreachable controls at the target viewport sizes
  or 200% text zoom.
- Primary actions and state are equivalent across widths; responsive layout does
  not silently remove functionality.
- Hover-only interactions have a coarse-pointer equivalent, and drag
  interactions have a touch-safe alternative.
- Scroll ownership is obvious: the document normally scrolls, while tables,
  modal bodies, code blocks, and intentional sheets contain only their own
  overflow.
- Resizing and rotation preserve user state and do not require a reload.
- English, German, and Arabic pass the page's automated smoke checks; every
  built-in theme passes visual review.

### Test coverage — required, not optional

A page family is not done until its Playwright coverage actually exercises its
responsive behaviour. "The route loads at 390 px without overflowing" is the
entry condition, not the finish line.

Coverage ships **in the same change** as the page work. The commands and the
artifacts are in "Acceptance criteria for a page-family agent" above; this
section is about *what the tests have to assert*.

1. **Scenarios live in the page's own spec.** Responsive scenarios go in
   `yaffo_ui_tests/specs/<feature>.yaml` alongside that page's existing
   behaviour scenarios — there is no separate responsive feature. A starting set
   is already written for every family; the owner extends it as the work
   uncovers more.
2. **Shared assertions come from the shared helper.** Import overflow
   diagnostics, viewport fit, the peer-panel contract, real touch drag, and
   coarse-pointer contexts from `generated_tests/_support/responsive.ts`. A page
   that re-implements an overflow check has forked the contract. A genuinely new
   shared assertion goes to the shared owner to add there.
3. **Every page family covers this minimum**, in its own spec:
   - the route renders without page-level horizontal overflow at 320, 390, 768,
     1024, and 1440 px;
   - each panel the page registers satisfies the peer-panel contract — closed on
     first paint, peer of Menu, mutually exclusive with it, restored to the page
     on desktop;
   - **state survives a resize through the breakpoint** without a reload: entered
     filter values, selections, open media, map centre and zoom, in-progress
     edits — whichever the page actually has;
   - every hover-only affordance is exercised through a coarse-pointer context,
     and every drag has its touch-safe alternative asserted;
   - scroll ownership: the page's tables, code blocks, and dialog bodies contain
     their own overflow rather than the document;
   - the page's own long-content cases — translated labels, filenames, paths,
     device names, person names, album and widget titles — do not widen it.
4. **A regression gets a scenario.** Any responsive bug found and fixed during
   the work earns a scenario naming the cause, so a later regeneration cannot
   quietly "simplify" the test back into passing.
5. **The family's pre-existing behaviour scenarios still pass.** Responsive work
   must not be bought by weakening the tests that were already there.

Every page family's responsive scenarios are now implemented in
`generated_tests/` and green in isolation. The milestone is not complete while
P5's acceptance review remains open, the Phase 5 matrix remains open, or the
integration gate has not been run as a single pass.
