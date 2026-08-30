# Responsive website plan

Status: **Proposed**

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

## Current-state audit

The base template already declares a responsive viewport, and a few areas have
narrow-screen rules: the timeline scrubber at 900 px, media detail at 768 px,
custom-page design at 900 px, pagination at 640 px, and sharing controls at
720 px. These are isolated adaptations rather than a site-wide layout system.
In particular, the global navigation and the recurring 250–280 px sidebar
layouts do not yet have a narrow-screen contract.

| Area | Current risk | Primary files |
| --- | --- | --- |
| Application shell | Nine top-level destinations, the Pages control, and the custom-page strip compete for one row; sticky height is consumed by several other layouts. | `yaffo/templates/base.html`, `yaffo/static/base.css`, `yaffo/static/pages/nav.css`, `yaffo/static/nav.js` |
| Shared layout and controls | Sidebars stay fixed width and sticky; headers, action groups, forms, tables, notifications, overlays, and modal footers can exceed narrow widths. | `yaffo/templates/_sidebar.html`, `yaffo/templates/components/`, `yaffo/static/sidebar.css`, `yaffo/static/form.css`, `yaffo/static/table.css`, `yaffo/static/button.css`, `yaffo/static/components/` |
| Library and albums | The gallery grid has adaptive tracks but a 250 px floor; filters, album navigation, selection actions, the timeline scrubber, and drag affordances need touch-safe alternatives. | `yaffo/templates/index.html`, `yaffo/templates/_timeline_sections.html`, `yaffo/templates/albums/`, `yaffo/static/index.css`, `yaffo/static/albums/albums.css`, `yaffo/static/media/` |
| Media detail | The existing 768 px stack is a useful start, but fixed viewport-height calculations, nested scrolling, metadata actions, face tools, video states, and landscape phones need verification. | `yaffo/templates/media/view.html`, `yaffo/static/media/view.css`, `yaffo/static/media/view.js` |
| Faces and people | Face assignment has dense sidebar actions and a 320 px hover preview; the people list is an unwrapped six-column table. | `yaffo/templates/faces/`, `yaffo/templates/people/`, `yaffo/static/faces/index.css`, `yaffo/static/people/` |
| Locations | The map, filter sidebar, 380 px selection panel, hover/click affordances, and dynamic map sizing currently assume desktop space. | `yaffo/templates/locations/list.html`, `yaffo/static/locations/list.css`, `yaffo/static/locations/list.js` |
| Utilities | Utility navigation remains a fixed sidebar; stats, scan results, duplicate review, automation headers, code views, trigger editors, and run tables are dense. | `yaffo/templates/utilities/`, `yaffo/static/utilities/` |
| Sharing | Sharing navigation is fixed width. Pairing, device, grant, transfer, and remote-file controls adapt unevenly; the remote gallery inherits the library concerns. | `yaffo/templates/sharing/`, `yaffo/static/sharing/sharing.css`, `yaffo/static/sharing/` |
| Settings and themes | Path rows and file-browser forms contain long unbreakable values; theme and utility navigation repeat the fixed-sidebar pattern. | `yaffo/templates/settings/`, `yaffo/templates/themes_page/`, `yaffo/static/settings/index.css`, `yaffo/static/themes_page/index.css` |
| Custom pages | The editor stacks below 900 px, but GridStack behavior, resize handles, widget iframes, presentation mode, and generated widget content need an explicit small-screen policy. | `yaffo/templates/pages/`, `yaffo/static/pages/detail.css`, `yaffo/static/pages/grid.js` |
| Error and demo states | Standalone error/security/demo pages must share the same width, zoom, safe-area, and long-copy guarantees. | `yaffo/templates/404.html`, `yaffo/templates/500.html`, `yaffo/templates/db_error.html`, `yaffo/templates/security/`, `yaffo/templates/demo/`, `yaffo/static/error.css`, `yaffo/static/demo-mode.css` |
| Themes and localization | Theme skins override structural selectors, while German, Hindi, and Arabic expose wrapping and direction assumptions that English does not. | `yaffo/static/themes/`, `yaffo/static/locales/`, `yaffo/translations/` |

Before implementation, capture representative desktop and narrow screenshots
for every row above and log each failure as one of: viewport overflow, clipped
content, unreachable interaction, undersized target, broken reading order, or
lost state. This establishes a reproducible baseline and avoids fixing only the
most visible pages.

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

- Add an accessible menu button to `base.html`. At narrow widths, place the
  top-level destinations and Pages controls in a disclosed menu or drawer; do
  not depend on horizontal scrolling to discover primary navigation.
- Preserve the active destination, custom-page edit action, New Page action,
  and the persisted Pages expanded/collapsed preference.
- In `nav.js`, manage `aria-expanded`, focus entry/return, Escape and outside
  dismissal, body scroll locking, resize across the breakpoint, and the
  published `--navbar-height` value. Closing or resizing the menu must not leave
  hidden items focusable.
- Keep the wide navigation visually unchanged and ensure all theme-specific nav
  decorations tolerate wrapped or drawer presentation.

### 3. Create one narrow-screen sidebar pattern

- Give filter, album, utility, sharing, and theme sidebars a common responsive
  DOM contract and initializer. On wide screens they remain sticky sidebars; on
  narrow screens they become an in-flow disclosure or modal sheet opened by a
  labeled button.
- Show an applied-filter count and keep Clear/Apply actions reachable without
  scrolling the whole page. Preserve form state when opening, closing, or
  crossing the breakpoint.
- Support Escape, focus return, backdrop dismissal, browser history/back when a
  sheet changes the visible UI state, and `aria-controls`/`aria-expanded`.
- Recalculate sticky offsets from the real navbar height. Avoid nested page
  scroll regions on narrow screens unless the component is a dialog or sheet.

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

1. Add a responsive Playwright project or dedicated responsive specs under
   `yaffo_ui_tests/generated_tests/responsive/` using the seeded application.
2. Add helpers that assert `scrollWidth <= clientWidth`, identify overflowing
   descendants, check that open dialogs/sheets fit the visual viewport, and
   exercise viewport resize without reloading.
3. Capture baselines for every page family in classic English, then stress the
   shared shell with German and Arabic and the most structurally divergent
   built-in themes.
4. Turn the support contract above into a review checklist so new responsive
   failures are not accepted as known debt during the rollout.

### Phase 1: Shell and shared components

1. Implement adaptive primary/custom-page navigation.
2. Implement the shared responsive sidebar/disclosure pattern and migrate
   `_sidebar.html`, albums, utilities, sharing, and themes to it.
3. Update global containers, page headers, action groups, forms, tables,
   pagination, modals, notifications, overlays, search/multi-select controls,
   selection bars, job progress, chat, and the file/folder pickers.
4. Verify standalone error, CSRF, database-error, and demo-disabled screens.

This phase must land before page-specific fixes so later work uses one stable
responsive vocabulary.

### Phase 2: Core photo workflows

1. **Library grid and timeline:** use fluid card tracks down to one column,
   preserve aspect ratios, keep favorite/video controls touchable, move or
   collapse filters through the shared sidebar pattern, and give the timeline
   scrubber a narrow-screen alternative that does not cover content.
2. **Albums:** adapt overview tiles, detail/edit actions, add-photo filters,
   selection mode, cover/share dialogs, and drag reordering. Provide explicit
   move controls or another keyboard/touch path so drag is never the only way to
   reorder.
3. **Media detail:** refine the existing stacked layout for portrait and
   landscape, use dynamic viewport units, keep the media visible while metadata
   is reachable, and verify faces, people, tags, favorites, location editing,
   missing-media states, and video playback.
4. **Remote gallery:** reuse the completed library behavior and then verify
   download-directory and remote-preview states.

### Phase 3: Organization and administration

1. **Faces:** collapse the assignment sidebar, scale the face grid fluidly,
   replace hover-only source previews on coarse pointers, and keep selection and
   keyboard shortcuts coherent when the layout changes.
2. **People:** provide a narrow card presentation for the six-column list,
   adapt add/edit dialogs, and verify a person's face gallery and filters.
3. **Settings:** stack file-browser/path controls, wrap long filesystem paths,
   adapt label chips and API-key controls, and keep destructive actions distinct.
4. **Utilities:** adapt index-photo stats/results and remove-duplicate review;
   keep result tables or photo groups locally scrollable without hiding their
   actions.
5. **Themes:** migrate navigation to the shared sidebar, wrap draft/publish
   actions, and verify theme generation chat at all target widths.

### Phase 4: Spatial and authoring workflows

1. **Locations:** make the map the primary narrow-screen surface; present the
   selected-cluster details as a bottom sheet or full-width panel, expose all
   hover behavior through click/focus, call the OpenLayers size update after
   every layout transition, and preserve map center, zoom, selection, and
   unsaved assignment state across resize.
2. **Automations:** stack editor/chat/code areas, adapt trigger builders and code
   toggles, contain code and test-result tables, and keep the full action set
   discoverable in long locales.
3. **Custom pages:** define GridStack column counts and minimum widget heights
   for wide, intermediate, and narrow canvases. In design mode, disable or adapt
   drag/resize gestures that conflict with page scrolling and provide explicit
   move/resize controls. In presentation mode, reflow widgets in reading order.
   Ensure widget iframes receive their actual container size and require
   generated widget HTML to be internally responsive.
4. **Sharing:** finish pairing QR/code, device/grant forms, file pulls, transfer
   status, and long device/path content after the shared navigation and table
   patterns are stable.

### Phase 5: Cross-cutting hardening

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
