# Responsive website plan

Status: **In progress — shared shell and Home pilot implemented**

Last updated: **2026-08-30**

This plan covers every server-rendered Yaffo page, shared component, built-in
theme, supported locale, and client-side interaction. The goal is one adaptive
interface rather than separate desktop and mobile implementations.

## Outcomes

The work is complete when:

- every page works without page-level horizontal scrolling at a 320 CSS pixel
  viewport; intentionally wide content such as a data table may scroll inside a
  clearly bounded container;
- primary content, navigation, filters, forms, actions, dialogs, and transient
  UI remain reachable with touch, mouse, and keyboard;
- layouts respond to the space available to the component instead of assuming
  a particular device;
- resizing or rotating the viewport does not lose selections, entered values,
  scroll position, open media, map state, or in-progress work;
- the interface remains usable at 200% text zoom and with long translated text;
- left-to-right and right-to-left layouts work without maintaining separate
  markup;
- the classic, darkroom, memphis, neobrutalist, photos-app, and scrapbook themes
  preserve their visual identity at every supported width; and
- automated responsive checks prevent new overflow and interaction regressions.

## Current implementation status

The responsive foundation is now running in the application, but the rollout is
not complete. The shell and Home page are the reference implementation. Other
page families have smoke coverage and several targeted adaptations, but they
have not all received a complete interaction, locale, and theme review.

| Area | Status | Evidence and remaining gap |
| --- | --- | --- |
| Guardrails | **In progress** | `generated_tests/responsive/responsive.spec.ts` has 20 passing Chromium cases covering 320, 390, 768, 1024, and 1440 px, overflow diagnostics, short landscape, doubled root text, RTL, coarse pointers, and resize behavior. Representative visual baselines, long-locale fixtures, reduced motion, and the complete cross-theme matrix remain. |
| Application shell | **Implemented, needs hardening** | Mobile Menu is accessible, mutually exclusive with page actions, hidden on desktop, closed on first paint, and preserves the Pages behavior. Full keyboard/focus auditing, safe-area handling, and visual review across all themes/locales remain. |
| Home filters | **Pilot complete** | Filters is a peer of Menu, uses the same navbar panel host, preserves the live form DOM across breakpoints, removes desktop `.sidebar` chrome on mobile, has an active state, and supports touch filter reordering. Do not migrate other pages to this pattern until the product owner explicitly confirms the Home pilot. |
| Home header and pagination | **Implemented** | Grid/Timeline is vertically centered. Pagination keeps text on desktop and uses one inline row of themed 44 px icon controls on mobile. Next-page navigation no longer flashes the menu. |
| Shared components | **Partial** | Narrow layouts exist for common containers, tables, modals, forms, actions, and pagination. Popovers, searchable controls, notifications, on-screen-keyboard behavior, focus trapping, and every shared component still need systematic review. |
| Core workflows | **Partial** | Album/widget explicit order controls, coarse-pointer face previews, people cards, media/detail route containment, and location resize behavior are covered. Complete workflow-level reviews are still outstanding. |
| Validation | **Partial** | Responsive Playwright, UI-test TypeScript, ESLint, i18n, and design-token checks pass. The full Playwright suite has not been run for this milestone. The app-wide JavaScript typecheck still has a pre-existing `yaffo/static/pages/grid.js:234` `Element.focus()` typing failure. |

Current milestone estimate: the shared foundation and first vertical slice are
substantially complete; most page-family rollout and cross-cutting hardening
remain. Passing route containment is a smoke signal, not completion of that page
family.

## Conventions established by the Home pilot

- On narrow screens, page-specific actions such as **Filters** are peers of
  **Menu** in the main navbar. Do not put page panels inside the Menu panel and
  do not create nested collapse panels.
- A page may register multiple peer actions. Only one of Menu or any page panel
  may be expanded at a time. Drive state with `aria-expanded`; Escape and
  outside click close the active surface and restore focus where appropriate.
- The Home implementation is a pilot. Confirm with the product owner before
  applying this panel model elsewhere in the app.
- Reuse the existing panel DOM. Move it into the navbar host at the responsive
  breakpoint and restore it to a marker on desktop so entered values, selected
  options, and component state survive resize.
- Closed mobile UI must be correct in CSS before JavaScript initializes. This
  prevents Menu or panel content from flashing during full-page navigation.
- A mobile filter panel renders its contents without desktop `.sidebar`
  background, padding, radius, or shadow. Avoid empty framing and nested scroll
  regions.
- Menu and page-action buttons use an 8 px gap, at least a 44 px target, a clear
  theme-token active state, and theme-aware icons following
  `docs/development/icons.md` (shared outline mask plus neobrutalist override).
- Shared pagination retains localized text labels on desktop and renders only
  accessible First/Previous/Next/Last icons at 640 px and below. All controls
  stay on one row at the 320 px minimum width.
- Drag interactions need a real touch path and a keyboard/direct-control
  alternative where practical. Touch reordering uses Pointer Events, captures
  the pointer on a stable ancestor rather than the moving row, reserves a 44 px
  handle, and is tested with Chrome's real emulated touch stream rather than
  only synthetic DOM events.
- Keep structural responsive CSS in the shared or owning page/component
  stylesheet. Themes skin states with tokens and icon art; they do not define a
  separate responsive layout.

## Current-state audit and remaining risks

The original audit found isolated narrow-screen rules without a site-wide
contract. The branch now has a shared responsive layer, adaptive navigation,
and route-level containment coverage. The table is retained as the rollout
inventory; its risk column now describes what remains rather than the original
starting state.

| Area | Current risk | Primary files |
| --- | --- | --- |
| Application shell | The narrow Menu and page-panel host are implemented. Remaining work is focus/scroll-lock hardening, safe areas, long locales, and visual verification of every theme decoration. | `yaffo/templates/base.html`, `yaffo/static/base.css`, `yaffo/static/pages/nav.css`, `yaffo/static/nav.js`, `yaffo/static/responsive.css` |
| Shared layout and controls | The responsive layer covers common containment and touch targets, and Home proves the peer-panel model. Remaining pages must not inherit the older generated nested-collapse behavior without review; overlays, pickers, notifications, and keyboard/viewport edge cases remain. | `yaffo/templates/_sidebar.html`, `yaffo/templates/components/`, `yaffo/static/sidebar.css`, `yaffo/static/form.css`, `yaffo/static/table.css`, `yaffo/static/button.css`, `yaffo/static/components/`, `yaffo/static/responsive.js` |
| Library and albums | Home filters, header, pagination, and basic grid containment are implemented; album/widget direct order controls exist. Timeline scrubber alternatives, full media states, album dialogs/selections, and end-to-end state preservation still need review. | `yaffo/templates/index.html`, `yaffo/templates/_timeline_sections.html`, `yaffo/templates/albums/`, `yaffo/static/index.css`, `yaffo/static/albums/albums.css`, `yaffo/static/media/` |
| Media detail | The existing 768 px stack is a useful start, but fixed viewport-height calculations, nested scrolling, metadata actions, face tools, video states, and landscape phones need verification. | `yaffo/templates/media/view.html`, `yaffo/static/media/view.css`, `yaffo/static/media/view.js` |
| Faces and people | Coarse pointers can open face previews and the people table becomes labeled cards. Assignment workflows, shortcut ordering, dialogs, long content, and all focus/selection states remain. | `yaffo/templates/faces/`, `yaffo/templates/people/`, `yaffo/static/faces/index.css`, `yaffo/static/people/` |
| Locations | Map resize and narrow selection-panel containment are covered. The intended bottom-sheet interaction, full touch/focus parity, map-state preservation, and all assignment flows remain. | `yaffo/templates/locations/list.html`, `yaffo/static/locations/list.css`, `yaffo/static/locations/list.js` |
| Utilities | Utility navigation remains a fixed sidebar; stats, scan results, duplicate review, automation headers, code views, trigger editors, and run tables are dense. | `yaffo/templates/utilities/`, `yaffo/static/utilities/` |
| Sharing | Sharing navigation is fixed width. Pairing, device, grant, transfer, and remote-file controls adapt unevenly; the remote gallery inherits the library concerns. | `yaffo/templates/sharing/`, `yaffo/static/sharing/sharing.css`, `yaffo/static/sharing/` |
| Settings and themes | Path rows and file-browser forms contain long unbreakable values; theme and utility navigation repeat the fixed-sidebar pattern. | `yaffo/templates/settings/`, `yaffo/templates/themes_page/`, `yaffo/static/settings/index.css`, `yaffo/static/themes_page/index.css` |
| Custom pages | The editor stacks and explicit move/resize controls have responsive coverage. GridStack policies, widget iframe sizing, presentation reading order, generated content, and the existing JavaScript typing failure remain. | `yaffo/templates/pages/`, `yaffo/static/pages/detail.css`, `yaffo/static/pages/grid.js` |
| Error and demo states | Standalone error/security/demo pages must share the same width, zoom, safe-area, and long-copy guarantees. | `yaffo/templates/404.html`, `yaffo/templates/500.html`, `yaffo/templates/db_error.html`, `yaffo/templates/security/`, `yaffo/templates/demo/`, `yaffo/static/error.css`, `yaffo/static/demo-mode.css` |
| Themes and localization | Theme skins override structural selectors, while German, Hindi, and Arabic expose wrapping and direction assumptions that English does not. | `yaffo/static/themes/`, `yaffo/static/locales/`, `yaffo/translations/` |

For every unfinished row, capture representative desktop and narrow screenshots
before changing it and log each failure as one of: viewport overflow, clipped
content, unreachable interaction, undersized target, broken reading order, or
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

The contract also includes keyboard-only use, coarse-pointer use, 200% text
zoom, reduced motion, safe-area insets, and both `dir="ltr"` and `dir="rtl"`.
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

- **Implemented:** `base.html` has an accessible mobile Menu; Home adds Filters
  through the page-action block. The top-level destinations, Pages controls,
  active destination, custom-page actions, and Pages preference remain intact.
- **Implemented:** `nav.js` enforces mutual exclusion, moves/restores live panel
  DOM, manages `aria-expanded`, Escape/outside dismissal, focus entry/return,
  breakpoint changes, and the published navbar-height variables.
- **Remaining:** audit body scroll behavior, safe-area insets, hidden focusable
  content, and every theme/locale. Keep the wide navigation visually unchanged.
- **Approval gate:** do not add page panels beyond Home until the Home behavior
  is explicitly approved for app-wide rollout.

### 3. Create one narrow-screen page-panel pattern

- Use Home as the reference: page sections are labeled peer buttons beside
  Menu, not nested disclosures inside the page or Menu. A page may expose more
  than one section, but only one page panel or Menu is visible at a time.
- Keep the wide sticky sidebar presentation and move the same live DOM into the
  shared navbar panel host on narrow screens. Preserve form/component state
  across open, close, resize, and rotation.
- Define how pages with Actions plus Filters map those sections to peer buttons.
  Applied-filter counts and compact Clear/Apply affordances remain open design
  work and should be settled in the shared contract before mass migration.
- Support Escape, focus return, outside dismissal, and
  `aria-controls`/`aria-expanded`. Evaluate browser history/back only if a panel
  becomes a sheet or otherwise represents navigation state.
- Recalculate sticky offsets from the measured navbar height. Avoid nested page
  scroll regions; mobile page panels should drop desktop sidebar framing.

### 4. Harden shared components

- Page headers and action bars: wrap predictably, keep the title readable, and
  make high-priority actions full-width only when the available space requires
  it.
- Forms: stack `.form-row` labels and controls, let paths and code wrap or scroll
  within their field, and keep validation messages adjacent to the input.
- Tables: wrap every truly tabular surface in a focusable, labeled horizontal
  scroller with a visible affordance. Convert the people list and other
  row-action-heavy tables to labeled cards at narrow widths when comparing
  columns is not the primary task. Never hide data solely to make a row fit.
- Modals and pickers: use edge gutters on tablets and a full-height sheet on
  narrow screens; keep `.modal-body` as the only scroll region, wrap footer
  actions, account for the on-screen keyboard, and retain focus trapping.
- Popovers, searchable selects, multi-selects, tooltips, and notifications:
  clamp to the visual viewport, flip placement when needed, use click/focus
  alternatives for hover-only content, and allow long localized content to
  wrap.
- Pagination, selection bars, job cards, chat, file browser, date/distance
  inputs, and cron builder: verify wrapping, scroll ownership, focus order, and
  minimum touch targets as shared components before page-specific work.
- Apply reduced-motion preferences to layout transitions, drawers, modals,
  overlays, card hover motion, and loading animations.

## Page-family rollout

Implement and review each phase as a complete vertical slice: markup, CSS,
behavior, localization, theme compatibility, and Playwright coverage ship
together.

### Phase 0: Baseline and guardrails

1. **Done:** add dedicated responsive specs under
   `yaffo_ui_tests/generated_tests/responsive/` using the seeded application.
2. **Partial:** overflow diagnostics and resize-without-reload coverage exist.
   Add reusable dialog/sheet viewport and hidden-focusable helpers, then move
   page-family cases out of the growing shared spec into owned spec files.
3. **Remaining:** capture baselines for every page family in classic English,
   then stress the shared shell with German and Arabic and the most structurally
   divergent built-in themes.
4. **Remaining:** turn the support contract above into a review checklist so
   new responsive failures are not accepted as known debt during the rollout.

### Phase 1: Shell and shared components

1. **Implemented, hardening remains:** adaptive primary/custom-page navigation.
2. **Home pilot implemented; approval required:** replace the old generic
   nested sidebar/disclosure rollout with the peer page-panel contract. After
   approval, migrate albums, utilities, sharing, themes, and other pages one
   page family at a time.
3. **Partial:** global containers, page headers, action groups, forms, tables,
   pagination, modal layout, and touch targets have initial rules. Complete the
   notification, overlay, search/multi-select, selection bar, job progress,
   chat, file/folder picker, focus-trap, and on-screen-keyboard audits.
4. **Remaining:** verify standalone error, CSRF, database-error, demo-disabled,
   and other shell-light screens.

The shared contract and a page-family change must not be edited concurrently by
different agents. Freeze shared behavior first, then let page owners consume it.

### Phase 2: Core photo workflows

1. **Library grid and timeline — partial:** Home filters, grid containment,
   view switch, pagination, favorite/video touch containment, and basic timeline
   layout exist. Finish the timeline scrubber alternative, streaming/rotation
   state, media loading behavior, and full workflow/theme/locale review.
2. **Albums — partial:** direct move controls and basic route containment exist.
   Finish overview tiles, detail/edit actions, add-photo filters,
   selection mode, cover/share dialogs, and drag reordering. Provide explicit
   move controls or another keyboard/touch path so drag is never the only way to
   reorder.
3. **Media detail — partial:** route containment exists. Refine the stacked
   layout for portrait and landscape, use dynamic viewport units, keep the media
   visible while metadata is reachable, and verify faces, people, tags,
   favorites, location editing, missing-media states, and video playback.
4. **Remote gallery — remaining:** reuse the completed library behavior and
   verify download-directory and remote-preview states.

### Phase 3: Organization and administration

1. **Faces — partial:** the grid is contained and coarse pointers can open
   source previews. Finish the assignment panel design, shortcut reordering,
   selection, dialogs, and keyboard behavior.
2. **People — partial:** the six-column list has a labeled mobile card
   presentation. Finish add/edit dialogs, person face gallery, filters, and long
   content.
3. **Settings — remaining:** stack file-browser/path controls, wrap long
   filesystem paths, adapt label chips and API-key controls, and keep destructive
   actions distinct.
4. **Utilities — remaining:** adapt index-photo stats/results and
   remove-duplicate review; keep result tables or photo groups locally
   scrollable without hiding their actions.
5. **Themes — remaining and approval-gated for panel migration:** migrate
   navigation to the peer-panel contract, wrap draft/publish
   actions, and verify theme generation chat at all target widths.

### Phase 4: Spatial and authoring workflows

1. **Locations — partial:** container resize and selection-panel smoke coverage
   exist. Make the map the primary narrow-screen surface; present the
   selected-cluster details as a bottom sheet or full-width panel, expose all
   hover behavior through click/focus, call the OpenLayers size update after
   every layout transition, and preserve map center, zoom, selection, and
   unsaved assignment state across resize.
2. **Automations — remaining:** stack editor/chat/code areas, adapt trigger
   builders and code toggles, contain code and test-result tables, and keep the
   full action set discoverable in long locales.
3. **Custom pages — partial:** explicit move/resize controls and narrow route
   containment exist. Define GridStack column counts and minimum widget heights
   for wide, intermediate, and narrow canvases. In design mode, disable or adapt
   drag/resize gestures that conflict with page scrolling and provide explicit
   move/resize controls. In presentation mode, reflow widgets in reading order.
   Ensure widget iframes receive their actual container size and require
   generated widget HTML to be internally responsive. Resolve the existing
   `pages/grid.js` JavaScript typing failure while this area is owned.
4. **Sharing — remaining:** finish pairing QR/code, device/grant forms, file
   pulls, transfer status, and long device/path content after the shared
   navigation and table patterns are stable.

### Phase 5: Cross-cutting hardening

All Phase 5 items remain milestone exit work:

1. Run every responsive smoke case across all built-in themes. Remove structural
   theme overrides or add narrowly scoped compatibility rules where a decorative
   effect changes geometry.
2. Run the matrix in English, German, and Arabic, then spot-check the remaining
   supported locales. Fix truncation, bidirectional text, logical alignment, and
   focus order rather than shortening translations.
3. Test 200% text zoom, keyboard-only navigation, coarse pointer, reduced
   motion, portrait/landscape rotation, and short viewport heights.
4. Profile image-heavy grids, maps, and widget pages at narrow widths. Responsive
   work must not cause duplicate media downloads, layout thrashing, or expensive
   resize loops; debounce only work that measurement shows is costly.
5. Update developer documentation with the final sidebar, navigation,
   breakpoint, table, and testing conventions so future pages inherit them.

## Parallel execution plan

### Shared gates and ownership

The page-family work is parallelizable only if shared files have one owner.
Agents must not independently evolve the shell contract while also migrating
pages against it.

1. **S0 — Product decision (shared gate):** review the completed Home pilot and
   explicitly approve, revise, or reject peer navbar panels for the rest of the
   app. Until this decision is recorded, agents may audit other sidebars and fix
   page-local layout problems, but must not migrate their navigation/panels.
2. **S1 — Freeze the shared contract (one agent, serialized):** after S0, define
   the registration contract for multiple page actions, Actions-plus-Filters
   ordering, active state, focus behavior, DOM restoration, panel scrolling,
   applied-filter counts, and breakpoint behavior. Reconcile the legacy generic
   `.responsive-panel-toggle` initializer with the approved model instead of
   leaving two competing mobile navigation systems.
3. **S2 — Shared component hardening (one shared owner):** own changes to
   `yaffo/templates/base.html`, `yaffo/templates/_sidebar.html`,
   `yaffo/static/nav.js`, `yaffo/static/responsive.js`,
   `yaffo/static/responsive.css`, `yaffo/static/base.css`, shared component
   templates/styles/scripts, icon registration, and
   `yaffo/static/types/global.d.ts`. Page agents report a shared-component need
   to this owner rather than patching these files concurrently.
4. **S3 — Shared test infrastructure (one owner, may run alongside S2):** extract
   overflow, viewport-fit, hidden-focusable, touch-drag, and resize helpers from
   the monolithic responsive spec. Establish one spec per page family so page
   agents do not all edit `generated_tests/responsive/responsive.spec.ts`.

S1 must precede any non-Home panel migration. S2 shared primitives must land
before a page agent consumes them. S3 can proceed in parallel because its file
ownership is limited to test helpers and new spec scaffolding.

### Independent page-family tasks

After the relevant shared primitive is stable, each row below can be assigned
to an independent agent. Each agent owns its page templates, page-specific CSS
and JavaScript, fixtures, and a dedicated responsive spec. Page-local rules stay
in the owning stylesheet; edits to the shared file set above go through S2.

| Task | Independent scope and acceptance target | Primary ownership | Shared dependency |
| --- | --- | --- | --- |
| **P1 — Library, timeline, and media detail** | Finish grid/timeline behavior, scrubber alternative, rotation/state preservation, loading, video, metadata, faces/tags/location editing, and portrait/landscape visual review. | `yaffo/templates/index.html`, `yaffo/templates/_timeline_sections.html`, `yaffo/templates/media/`, `yaffo/static/index.css`, `yaffo/static/media/`, new library/media responsive spec | Uses the already-approved Home panel pilot; coordinate any shared pagination/modal changes through S2. |
| **P2 — Albums** | Complete overview, detail/edit, add-photo filters, selection, cover/share dialogs, and keyboard/touch reorder paths at every contract viewport. | `yaffo/templates/albums/`, `yaffo/static/albums/`, new albums responsive spec | Panel migration waits for S0/S1; shared selection/modal issues go to S2. |
| **P3 — Faces and people** | Finish assignment actions, shortcut reordering, selection, dialogs, person galleries, filters, long names, and keyboard/coarse-pointer parity. | `yaffo/templates/faces/`, `yaffo/templates/people/`, `yaffo/static/faces/`, `yaffo/static/people/`, new faces/people responsive spec | Panel migration waits for S0/S1; shared table/card or touch-drag changes go to S2. |
| **P4 — Locations** | Deliver the narrow map plus bottom-sheet/full-width selection experience, click/focus equivalents, reliable OpenLayers resizing, and center/zoom/selection/unsaved-state preservation. | `yaffo/templates/locations/`, `yaffo/static/locations/`, new locations responsive spec | Sheet/modal contract and any shared panel entry point come from S1/S2. |
| **P5 — Utilities and automations** | Adapt utility navigation, stats/results, duplicate review, automation editor/chat/code, trigger builders, run tables, and long-locale action discovery. | `yaffo/templates/utilities/`, `yaffo/static/utilities/`, automation templates/styles/scripts, new utilities/automations responsive specs | Navigation migration waits for S0/S1; code/table/chat primitives come from S2. |
| **P6 — Sharing and remote gallery** | Complete pairing, QR/code, device/grant forms, remote filters/previews, file pulls, transfers, pagination, and long device/path behavior. | `yaffo/templates/sharing/`, `yaffo/static/sharing/`, new sharing responsive spec | Reuse final Library behavior from P1; navigation/table/picker primitives come from S1/S2. |
| **P7 — Settings, themes, and standalone states** | Adapt paths, file browser, labels, API keys, destructive actions, theme draft/publish/chat, and error/security/demo screens; perform long-copy checks. | `yaffo/templates/settings/`, `yaffo/templates/themes_page/`, standalone templates, `yaffo/static/settings/`, `yaffo/static/themes_page/`, `yaffo/static/error.css`, `yaffo/static/demo-mode.css`, dedicated specs | Theme navigation migration waits for S0/S1; shared file-browser/modal/chat issues go to S2. Do not modify theme skins except for page-specific verified compatibility fixes. |
| **P8 — Custom pages and widgets** | Finalize GridStack breakpoints, design-mode gesture policy, direct controls, presentation reading order, iframe sizing, and generated-widget responsiveness; fix the existing `grid.js:234` typing error. | `yaffo/templates/pages/`, `yaffo/static/pages/detail.css`, `yaffo/static/pages/grid.js`, widget templates/runtime, new custom-pages responsive spec | Shared direct-control/icon patterns come from S2; otherwise independent. |

P1–P8 should each ship as a vertical slice: implementation, desktop regression,
responsive tests, keyboard/coarse-pointer checks, relevant locale/theme review,
and before/after screenshots. An agent must not mark a task complete merely
because its route passes the overflow smoke matrix.

### Shared integration and milestone exit

After the parallel page tasks merge, one integration owner performs work that
cannot be safely partitioned:

1. resolve shared CSS cascade and breakpoint conflicts introduced by combined
   page work;
2. run the complete Playwright suite plus app/UI TypeScript, ESLint, i18n, and
   design-token checks;
3. run the full built-in-theme and English/German/Arabic visual matrix at the
   contract viewports, including 200% text, RTL, reduced motion, short
   landscape, and on-screen-keyboard cases;
4. verify cross-page shell behavior, navbar height, focus restoration, browser
   back/forward behavior where applicable, and no first-paint flashes; and
5. update this document's status table and conventions after shared behavior is
   proven, then remove obsolete responsive paths rather than retaining two
   systems.

## Verification strategy

Keep the existing desktop Chromium project as the behavior regression suite.
Add focused responsive coverage rather than replaying every destructive or
long-running end-to-end scenario at every viewport.

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
- accessibility assertions for names, expanded state, focus trapping/return,
  hidden focusable content, DOM reading order, and keyboard alternatives to
  pointer gestures.

Run targeted specs while implementing a phase, then run the full
`yaffo_ui_tests` Playwright suite and the existing frontend checks before the
phase is considered complete. Visual review remains required for OpenLayers,
GridStack, media/video fitting, theme decoration, on-screen-keyboard behavior,
and RTL because geometry assertions alone cannot establish usability.

## Definition of done for each page

- No page-level horizontal overflow from 320 px through desktop widths.
- No clipped, overlapped, or unreachable controls at the target viewport sizes
  or 200% text zoom.
- Primary actions and state are equivalent across widths; responsive layout does
  not silently remove functionality.
- Focus order follows visual and reading order; opening and closing disclosed UI
  moves and restores focus correctly.
- Hover interactions have focus and coarse-pointer equivalents, and drag
  interactions have keyboard/touch-safe alternatives.
- Scroll ownership is obvious: the document normally scrolls, while tables,
  modal bodies, code blocks, and intentional sheets contain only their own
  overflow.
- Resizing and rotation preserve user state and do not require a reload.
- English, German, and Arabic pass the page's automated smoke checks; every
  built-in theme passes visual review.
- Relevant responsive Playwright coverage lands in the same change as the page
  migration, and the desktop suite remains green.
