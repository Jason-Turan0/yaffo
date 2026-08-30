# Responsive website plan

Status: **In progress — shared gates closed; page-family rollout ready to split**

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
| Guardrails | **In progress** | Responsive coverage is defined as scenarios inside each page family's `yaffo_ui_tests/specs/*.yaml`, with shared assertions extracted to `generated_tests/_support/responsive.ts` (overflow diagnostics, viewport fit, panel contract, touch drag). **The Playwright code for those scenarios has not been generated yet — every page family owns generating, committing and passing its own** (see "Test coverage" in the definition of done). Representative visual baselines, long-locale fixtures, and the complete cross-theme matrix remain. |
| Application shell | **Implemented** | Mobile Menu is mutually exclusive with page actions, hidden on desktop, closed on first paint, and preserves the Pages behavior. Escape ownership between a panel and a dialog opened inside it, safe-area insets, and on-screen-keyboard handling are in. Visual review across all themes/locales remains. |
| Page panels | **Contract frozen; every sidebar migrated** | The Home pilot was approved for app-wide rollout on 2026-08-30. All eleven page sidebars now register through `components/nav_panel.html`, the legacy `.responsive-panel-toggle` initializer and `static/responsive.js` are deleted, and the applied-filter count badge is server-rendered. Page owners refine *content* inside their panels; the contract itself is S2-owned. |
| Home header and pagination | **Implemented** | Grid/Timeline is vertically centered. Pagination keeps text on desktop and uses one inline row of themed 44 px icon controls on mobile. Next-page navigation no longer flashes the menu. |
| Shared components | **Hardened** | Narrow layouts exist for common containers, tables, modals, forms, actions, and pagination. Notifications, overlays, tooltips and dropdowns are clamped to the viewport and use logical properties; the selection bar clears the sticky navbar; viewport-bound surfaces use dynamic units and safe-area insets. Per-component visual review across themes remains. |
| Core workflows | **Partial** | Album/widget explicit order controls, coarse-pointer face previews, people cards, media/detail route containment, and location resize behavior are covered. Complete workflow-level reviews are still outstanding. |
| Validation | **Partial** | UI-test TypeScript, ESLint, spec validation, and design-token checks pass. The generated Playwright code for the new responsive scenarios does not exist yet. The app-wide JavaScript typecheck still has a pre-existing `yaffo/static/pages/grid.js:234` `Element.focus()` typing failure (owned by P8). |

Current milestone estimate: the shared foundation and first vertical slice are
substantially complete; most page-family rollout and cross-cutting hardening
remain. Passing route containment is a smoke signal, not completion of that page
family.

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
| Library and albums | Home filters, header, pagination, and basic grid containment are implemented; album/widget direct order controls exist. Timeline scrubber alternatives, full media states, album dialogs/selections, and end-to-end state preservation still need review. | `yaffo/templates/index.html`, `yaffo/templates/_timeline_sections.html`, `yaffo/templates/albums/`, `yaffo/static/index.css`, `yaffo/static/albums/albums.css`, `yaffo/static/media/` |
| Media detail | The existing 768 px stack is a useful start, but fixed viewport-height calculations, nested scrolling, metadata actions, face tools, video states, and landscape phones need verification. | `yaffo/templates/media/view.html`, `yaffo/static/media/view.css`, `yaffo/static/media/view.js` |
| Faces and people | Coarse pointers can open face previews and the people table becomes labeled cards. Assignment workflows, shortcut ordering, dialogs, long content, and selection states remain. | `yaffo/templates/faces/`, `yaffo/templates/people/`, `yaffo/static/faces/index.css`, `yaffo/static/people/` |
| Locations | Map resize and narrow selection-panel containment are covered. The intended bottom-sheet interaction, touch parity for hover-only behaviour, map-state preservation, and all assignment flows remain. | `yaffo/templates/locations/list.html`, `yaffo/static/locations/list.css`, `yaffo/static/locations/list.js` |
| Utilities | Utility navigation remains a fixed sidebar; stats, scan results, duplicate review, automation headers, code views, trigger editors, and run tables are dense. | `yaffo/templates/utilities/`, `yaffo/static/utilities/` |
| Sharing | Sharing navigation is fixed width. Pairing, device, grant, transfer, and remote-file controls adapt unevenly; the remote gallery inherits the library concerns. | `yaffo/templates/sharing/`, `yaffo/static/sharing/sharing.css`, `yaffo/static/sharing/` |
| Settings and themes | Path rows and file-browser forms contain long unbreakable values; theme and utility navigation repeat the fixed-sidebar pattern. | `yaffo/templates/settings/`, `yaffo/templates/themes_page/`, `yaffo/static/settings/index.css`, `yaffo/static/themes_page/index.css` |
| Custom pages | The editor stacks and explicit move/resize controls have responsive coverage. GridStack policies, widget iframe sizing, presentation reflow order, generated content, and the existing JavaScript typing failure remain. | `yaffo/templates/pages/`, `yaffo/static/pages/detail.css`, `yaffo/static/pages/grid.js` |
| Error and demo states | Standalone error/security/demo pages must share the same width, zoom, safe-area, and long-copy guarantees. | `yaffo/templates/404.html`, `yaffo/templates/500.html`, `yaffo/templates/db_error.html`, `yaffo/templates/security/`, `yaffo/templates/demo/`, `yaffo/static/error.css`, `yaffo/static/demo-mode.css` |
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
   touch drag, and coarse-pointer contexts are
   reusable helpers in `generated_tests/_support/responsive.ts`. The Playwright
   code for the new scenarios still has to be generated per family.
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

1. **Library grid and timeline — partial:** Home filters, grid containment,
   view switch, pagination, favorite/video touch containment, and basic timeline
   layout exist. Finish the timeline scrubber alternative, streaming/rotation
   state, media loading behavior, and full workflow/theme/locale review.
2. **Albums — partial:** direct move controls and basic route containment exist.
   Finish overview tiles, detail/edit actions, add-photo filters,
   selection mode, cover/share dialogs, and drag reordering. Provide explicit
   move controls or another touch-safe path so drag is never the only way to
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
   selection, and dialogs.
2. **People — partial:** the six-column list has a labeled mobile card
   presentation. Finish add/edit dialogs, person face gallery, filters, and long
   content.
3. **Settings — remaining:** stack file-browser/path controls, wrap long
   filesystem paths, adapt label chips and API-key controls, and keep destructive
   actions distinct.
4. **Utilities — remaining:** adapt index-photo stats/results and
   remove-duplicate review; keep result tables or photo groups locally
   scrollable without hiding their actions.
5. **Themes — partial:** navigation is migrated to the peer-panel contract.
   Wrap draft/publish actions and verify theme generation chat at all target
   widths.

### Phase 4: Spatial and authoring workflows

1. **Locations — partial:** container resize and selection-panel smoke coverage
   exist. Make the map the primary narrow-screen surface; present the
   selected-cluster details as a bottom sheet or full-width panel, expose all
   hover behavior through a coarse-pointer path, call the OpenLayers size update after
   every layout transition, and preserve map center, zoom, selection, and
   unsaved assignment state across resize.
2. **Automations — remaining:** stack editor/chat/code areas, adapt trigger
   builders and code toggles, contain code and test-result tables, and keep the
   full action set discoverable in long locales.
3. **Custom pages — partial:** explicit move/resize controls and narrow route
   containment exist. Define GridStack column counts and minimum widget heights
   for wide, intermediate, and narrow canvases. In design mode, disable or adapt
   drag/resize gestures that conflict with page scrolling and provide explicit
   move/resize controls. In presentation mode, reflow widgets in source order.
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
   same file. The Playwright code for those scenarios still has to be generated.

**Ownership going forward.** These files stay with the shared owner; a page
agent reports a need against them rather than patching them from a page task:
`yaffo/templates/base.html`, `yaffo/templates/_sidebar.html`,
`yaffo/templates/components/nav_panel.html`, `yaffo/static/nav.js`,
`yaffo/static/responsive.css`, `yaffo/static/base.css`,
`yaffo/static/components/` (modal, overlay, tooltip, notification, selection
bar, file browser), icon registration, `yaffo/static/types/global.d.ts`,
and `yaffo_ui_tests/generated_tests/_support/responsive.ts`.

### Independent page-family tasks

The shared gates are closed, so every row below can be assigned now. Each agent
owns its page templates, page-specific CSS and JavaScript, fixtures, and the
responsive scenarios already written into its own `yaffo_ui_tests/specs/*.yaml`
— plus generating and healing the Playwright code for them. Page-local rules
stay in the owning stylesheet; edits to the shared file set above go through the
shared owner. Every page's panel is already registered; what remains per family
is the *content and interaction* inside it.

| Task | Independent scope and acceptance target | Primary ownership | Shared dependency |
| --- | --- | --- | --- |
| **P1 — Library, timeline, and media detail** | Finish grid/timeline behavior, scrubber alternative, rotation/state preservation, loading, video, metadata, faces/tags/location editing, and portrait/landscape visual review. | `yaffo/templates/index.html`, `yaffo/templates/_timeline_sections.html`, `yaffo/templates/media/`, `yaffo/static/index.css`, `yaffo/static/media/` | Panels registered. Coordinate shared pagination/modal changes through the shared owner. Spec: `specs/photo_gallery.yaml`, `specs/photo_details.yaml`. |
| **P2 — Albums** | Complete overview, detail/edit, add-photo filters, selection, cover/share dialogs, and touch-safe reorder paths at every contract viewport. | `yaffo/templates/albums/`, `yaffo/static/albums/` | Panels registered (`albums-nav`, `album-add-filters`). Shared selection/modal issues go to the shared owner. Spec: `specs/albums.yaml`. |
| **P3 — Faces and people** | Finish assignment actions, shortcut reordering, selection, dialogs, person galleries, filters, long names, and coarse-pointer parity. | `yaffo/templates/faces/`, `yaffo/templates/people/`, `yaffo/static/faces/`, `yaffo/static/people/` | Panels registered (`faces-actions`/`faces-filters`, `person-faces-*`). Shared table/card or touch-drag changes go to the shared owner. Specs: `specs/face_assignment.yaml`, `specs/people.yaml`. |
| **P4 — Locations** | Deliver the narrow map plus bottom-sheet/full-width selection experience, coarse-pointer equivalents for hover-only behaviour, reliable OpenLayers resizing, and center/zoom/selection/unsaved-state preservation. | `yaffo/templates/locations/`, `yaffo/static/locations/` | Panel registered (`locations-filters`). The bottom-sheet variant is still an open shared question — raise it with the shared owner before inventing one. Spec: `specs/locations.yaml`. |
| **P5 — Utilities and automations** | Adapt utility navigation, stats/results, duplicate review, automation editor/chat/code, trigger builders, run tables, and long-locale action discovery. | `yaffo/templates/utilities/`, `yaffo/static/utilities/`, automation templates/styles/scripts | Panels registered (`utilities-nav`, `automations-nav`). Code/table/chat primitives come from the shared owner. Specs: `specs/index_photos.yaml`, `specs/remove_duplicates.yaml`, `specs/automations.yaml`. |
| **P6 — Sharing and remote gallery** | Complete pairing, QR/code, device/grant forms, remote filters/previews, file pulls, transfers, pagination, and long device/path behavior. | `yaffo/templates/sharing/`, `yaffo/static/sharing/` | Panels registered (`sharing-sidebar`, `remote-files-filters`). Reuse final library behaviour from P1. Spec: `specs/sharing.yaml`. |
| **P7 — Settings, themes, and standalone states** | Adapt paths, file browser, labels, API keys, destructive actions, theme draft/publish/chat, and error/security/demo screens; perform long-copy checks. | `yaffo/templates/settings/`, `yaffo/templates/themes_page/`, standalone templates, `yaffo/static/settings/`, `yaffo/static/themes_page/`, `yaffo/static/error.css`, `yaffo/static/demo-mode.css` | Panel registered (`themes-nav`). Shared file-browser/modal/chat issues go to the shared owner. Do not modify theme skins except for page-specific verified compatibility fixes. Specs: `specs/settings.yaml`, `specs/themes.yaml`. |
| **P8 — Custom pages and widgets** | Finalize GridStack breakpoints, design-mode gesture policy, direct controls, presentation reflow order, iframe sizing, and generated-widget responsiveness; fix the existing `grid.js:234` typing error. | `yaffo/templates/pages/`, `yaffo/static/pages/detail.css`, `yaffo/static/pages/grid.js`, widget templates/runtime | Shared direct-control/icon patterns come from the shared owner; otherwise independent. Spec: `specs/custom_pages.yaml`. |

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
2. **Regenerate the tests from it**, from `yaffo_ui_tests`:

   ```
   npm run generate:test specs/<feature>.yaml
   ```

   That generates the code *and* runs it. It writes three artifacts per feature,
   and all three are part of the change — an agent that commits one without the
   others leaves the next regeneration working from stale context:
   - `generated_tests/<feature>/<feature>.spec.ts` — the runnable tests;
   - `generated_tests/<feature>/<feature>.json` — generation metadata, whose
     `files[].code` must match the committed `.spec.ts`;
   - `generated_tests/<feature>/memories/progress.md` — the context discovered
     while generating (seeded data, ordering constraints, selector gotchas).
     Update it with what this round learned.

   Do not hand-author the `.spec.ts`. If a generated test is wrong, heal it —
   `npm run test:heal specs/<feature>.yaml` — and commit the healed output.
3. **The family's Playwright spec passes in isolation**, against a clean
   environment:

   ```
   npm run seed:build                                              # once
   npm run test:spec -- generated_tests/<feature>/<feature>.spec.ts
   ```

   `test:spec` takes the **generated spec path**, not the YAML. Build the seed
   cache once and let runs restore from it; `--fresh` re-runs the whole
   indexing, face-detection and labelling pipeline on every invocation and is
   not the normal path.
4. **The unit tests pass**, for whatever the change actually touched:
   - `npx vitest run` (from the repo root) whenever any `yaffo/static/**`
     JavaScript changed — the Playwright suite does not cover these;
   - `npm run typecheck:js` and `npm run lint` (repo root) for the same;
   - `python -m pytest tests/` for template, route, or Python changes;
   - `python -m pytest tests/yaffo/test_design_tokens.py` whenever any CSS
     changed — it is the drift guard that rejects raw colours in stylesheets;
   - `npx tsc --noEmit`, `npm run lint`, and `npm run validate:specs` from
     `yaffo_ui_tests` for spec and helper changes.
5. **No shared file is edited from a page task.** Changes to the shared set
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
**scenarios in the owning page family's `yaffo_ui_tests/specs/*.yaml`**, from
which the generator produces the Playwright code. There is no standalone
"responsive" feature — a page's narrow-screen behaviour is part of that page's
spec, which is also what keeps the P1–P8 tasks independent. Assertions that
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

The milestone is not complete while any family's responsive scenarios are still
unimplemented in `generated_tests/`.
