# Responsive conventions

How Yaffo's interface adapts to viewport size, and the rules a new page or
component is expected to follow. This is a standards document, not a plan: it
describes what the application does today and what is expected of work added to
it.

One adaptive interface, not a desktop build plus a mobile build. There is no
separate mobile template, no device sniffing, and no parallel stylesheet.

## Scope

This document owns **geometry at a viewport**: breakpoints, overflow, layout,
scroll ownership, state across resize, and coarse-pointer *paths*.

It does not own:

| Concern | Owner |
| --- | --- |
| Keyboard operation, focus management, ARIA, reading order | `accessibility.md` |
| Minimum target size (WCAG 2.5.8), 200% text zoom, `prefers-reduced-motion` | `accessibility.md` |
| Translation, pluralisation, locale data | `internationalization.md` |
| Icon art and theme icon overrides | `icons.md` |

The split is by evidence, not by subject. Responsive questions are settled by
measuring a box at a width. Accessibility questions are settled by a rule engine
and assistive technology. A control pushed off-screen is both, and the geometry
half is here.

## Breakpoints

Breakpoints are content-driven, but these are the shared boundaries in use.
Introduce a new one only when the content demonstrates the need, and put it in
the owning stylesheet rather than the global layer.

| Boundary | What changes |
| --- | --- |
| `max-width: 1200px` | The shell switches to the narrow model: Menu button, page panels hosted in the navbar, nav links as a two-column grid |
| `max-width: 900px` | Two-column page layouts become one column — media detail stacks, the locations selection panel becomes a centred modal |
| `max-width: 720px` | Isolated component wrapping |
| `max-width: 640px` | Single-column nav grid, sheet-style modals, icon-only pagination, stacked form rows |
| `max-width: 400px` | Navbar button text is hidden so Actions, Filters and Menu fit one row |

Capability queries carry as much weight as width:

- `@media (hover: none), (pointer: coarse)` — touch sizing and touch-only
  affordances.
- `@media (prefers-reduced-motion: reduce)` — applied globally; verification is
  `accessibility.md`'s.

Exercise these viewport classes during development:

| Viewport | Purpose |
| --- | --- |
| 320 × 568 | Minimum width and short-height stress case |
| 390 × 844 | Typical narrow portrait |
| 844 × 390 | Narrow landscape, constrained height |
| 768 × 1024 | Tablet portrait, intermediate wrapping |
| 1024 × 768 | Tablet landscape, desktop transition |
| 1440 × 900 | Desktop regression baseline |

## Non-negotiables

1. **No page-level horizontal scrolling at 320px.** Intentionally wide content —
   a data table, a code block — scrolls inside a clearly bounded container.
2. **The document is the scroller.** Tables, modal bodies, code blocks and
   deliberate sheets contain their own overflow. Nested page-level scroll
   regions are a defect: a pane that scrolls inside the page reads as the page
   and strands everything below it.
3. **Resize and rotation preserve state.** Selections, entered values, scroll
   position, open media, map centre and zoom, and in-progress edits survive a
   breakpoint crossing without a reload. Move live DOM; never re-render or clone
   a panel to relocate it.
4. **Nothing disappears to fit.** Layout may reorder, stack, or collapse
   behind a disclosure. It may not silently drop an action or a column of data.
5. **Every hover-only affordance has a tap path**, and every drag has a
   touch-safe alternative.
6. **Layout lives in the shared or owning stylesheet.** Themes skin the result.
7. **Long text does not widen the page.** Translated labels, filenames, paths,
   device names, person names, album and widget titles all wrap or ellipsise.
8. **Both directions work from one set of markup.** `dir="ltr"` and `dir="rtl"`.

## The panel contract

This is the application's single narrow-screen navigation model. There is no
second one — the older generic `.responsive-panel-toggle` initialiser and
`responsive.js` were removed rather than left to compete.

**Registration.** A panel is any element with `data-nav-panel` and an `id`. Its
peer button is rendered by `templates/components/nav_panel.html` into
`base.html`'s `nav_context_toggles` block, carries `data-nav-panel-toggle` and
`aria-controls="<panel id>"`, and is server-rendered so the closed narrow state
is correct on first paint. `_sidebar.html` takes a `panel_prefix` and registers
`<prefix>-actions` and `<prefix>-filters` as two separate panels.

**Ordering.** Toggles read in declaration order and **Menu always sorts last**.
A page with both declares Actions before Filters: Actions operates on the
current selection, so it is the likelier destination once items are picked.

**Rules:**

- Page panels are **peers of Menu** in the navbar, never nested inside Menu and
  never nested disclosures within the page.
- Only one of Menu or any page panel is open at a time. `aria-expanded` carries
  that state; Escape and outside click close the active surface.
- **Escape belongs to the topmost surface.** `nav.js` ignores Escape while a
  `.modal.active` is open, so a dialog opened from inside a panel is not
  dismissed along with its host.
- **Reuse the panel's existing DOM.** `nav.js` moves the live node into the
  navbar host at the breakpoint and restores it to a marker comment on desktop.
  The marker is a stable sibling of the desktop slot, so restoring never depends
  on a node an htmx swap may have replaced. Panels resolve by id on every use.
- A closed panel is `hidden`, not collapsed to zero size, so it takes no layout
  space and cannot be interacted with by accident.
- **Applied-filter counts are server-rendered** by the `applied_filter_count`
  Jinja global, so the badge never pops in after hydration. A multi-valued
  filter counts once; pagination, view, sort and scope keys do not count.
- Closed mobile UI must be correct in CSS before JavaScript initialises, or
  panel content flashes during full-page navigation.
- A panel in the navbar drops its desktop `.sidebar` framing — background,
  padding, radius, shadow. Framing a panel that is already inside one reads as
  an empty box and creates a second scroll region.
- **The panel host states its own surface and text colour.** It sits inside the
  navbar, so inheriting means taking the bar's background with the page's text
  colour — which renders same-on-same in any theme whose bar and page differ.
- Body scroll is deliberately **not** locked while a panel is open. The host is
  inside the sticky navbar and contains its own overscroll, so the page behind
  may scroll without the panel moving.
- Navbar buttons use an 8px gap, a theme-token active state, and theme-aware
  icons per `icons.md`. At 400px and below their visible text is hidden while
  accessible names remain.

## Layout conventions

- **Use the shared primitives.** Container gutters, readable content widths,
  stack gaps and grid minimums come from the global layer. Page styles opt in
  rather than duplicating media queries.
- **`min-width: 0` on flex and grid children** that hold user text, paths,
  tables or media. Without it the default `min-width: auto` refuses to shrink
  and the child sets the page width.
- **A grid child fills its cell.** A stretched cell holding a shrink-wrapped
  control is dead space that looks clickable. Watch for `inline-flex` arriving
  from a shared component rule.
- **Prefer intrinsic sizing to a breakpoint.** `repeat(auto-fill, minmax(Npx,
  1fr))` needs no media query. A breakpoint is a failure to express the rule
  intrinsically.
- **Logical properties** — `margin-inline-start`, `padding-block-end`,
  `inset-inline` — wherever a declaration affects flow. Keep physical
  coordinates for media overlays and map geometry, which describe pixels rather
  than reading order.
- **Breakpoints live in CSS.** JavaScript reacts to a semantic media query only
  when *behaviour* changes, never to do presentation.
- **Viewport-bound surfaces use `dvh` with a `vh` line before it** as the
  fallback. The document declares `viewport-fit=cover` and
  `interactive-widget=resizes-content`: use `env(safe-area-inset-*)` on anything
  flush to an edge, and expect the on-screen keyboard to shrink the layout
  viewport rather than scroll over it.
- **Filesystem paths, pairing codes and source code use
  `unicode-bidi: plaintext`** so they take direction from their own first strong
  character. Otherwise a leading `/` is a neutral character and an RTL page
  reorders the path to read backwards.

## Component conventions

- **Page headers and action bars** wrap predictably and keep the title readable.
  Actions go full-width only when the space requires it.
- **Forms** stack label and control at narrow widths. Paths and code wrap or
  scroll within their field; validation messages stay adjacent to their input.
- **Tables** wrap in a horizontal scroller with a visible affordance, or become
  labelled cards where comparing columns is not the primary task. Never hide
  data to make a row fit. A card-mode header row is hidden with the
  visually-hidden pattern rather than `display: none`, so it stays available to
  assistive technology.
- **Modals and pickers** use edge gutters on tablets and a full-height sheet on
  narrow screens. `.modal-body` is the only scroll region; footer actions wrap.
- **Popovers, selects, tooltips and notifications** clamp to the visual
  viewport, flip placement when there is no room, and let long localised content
  wrap. A CSS-only anchored element cannot stay inside the viewport — above
  640px `components/tooltip.js` positions and clamps; below it, the pinned bar
  is the presentation.
- **Overlays toggle.** A control that opens an overlay closes it on the second
  press, and opening a second overlay closes the first. Two live copies mean
  duplicate element ids.
- **Drag interactions** use Pointer Events, capture the pointer on a stable
  ancestor rather than the moving row, and always offer direct move controls as
  the touch path. Design-mode drag is disabled on touch.
- **Pagination** keeps localised text labels on desktop and renders
  First/Previous/Next/Last icons at 640px and below, on one row at 320px.

## Theme rules

Themes skin the result. They do not define a parallel responsive layout.

A theme may change colour, typography, borders, shadows, radius and icon art.
When a decorative effect changes **geometry**, it becomes a layout bug that the
shared layer's tests cannot see, because they pass in every other theme.

Watch for these, all of which have occurred:

- **A transform that paints outside its layout box.** A rotated element bleeds
  past its own edges by roughly `width × sin(angle)`, which scales with width —
  harmless on a small chip, overlapping on a full-width bar.
- **An offset shadow** painting into the element below it, which then covers it.
- **A theme gap overriding a shared gap**, so a control that is a *sibling* of a
  grid rather than a member of it spaces differently from everything around it.
- **A bar colour meeting page-level text colour**, which is how a panel ends up
  rendering text on its own colour.

Where a decorative effect genuinely needs to change geometry, add a narrowly
scoped compatibility rule in the theme's own stylesheet, bounded by the media
query where the problem exists. Do not weaken the shared rule.

`responsive.css` loads **after** the theme stylesheet, so a tie on specificity
resolves in the shared layer's favour. A theme rule with an attribute selector
plus a class beats a bare class in the shared layer; match its specificity to
win deliberately rather than by accident.

## Testing

Responsive coverage is written like every other suite here: **scenarios in the
owning page family's `yaffo_ui_tests/specs/*.yaml`**, with runnable Playwright
code under the matching `generated_tests/` directory. There is no standalone
"responsive" feature — a page's narrow-screen behaviour belongs to that page's
spec. The shell contract is exercised on Home, in `specs/photo_gallery.yaml`.

Shared assertions come from `generated_tests/_support/responsive.ts` — overflow
diagnostics, viewport fit, the peer-panel contract, real touch drag,
coarse-pointer contexts. **A page that re-implements an overflow check has
forked the contract.** A genuinely new shared assertion is added there.

Every page family covers this minimum:

- the route renders without page-level horizontal overflow at 320, 390, 768,
  1024 and 1440px;
- each registered panel satisfies the peer-panel contract — closed on first
  paint, peer of Menu, mutually exclusive, restored to the page on desktop;
- **state survives a resize through the breakpoint** without a reload, for
  whichever state the page actually has;
- every hover-only affordance is exercised in a coarse-pointer context, and
  every drag has its touch alternative asserted;
- scroll ownership: tables, code blocks and dialog bodies contain their own
  overflow;
- the page's long-content cases do not widen it.

**A fixed bug earns a scenario** naming the cause, so a later regeneration
cannot quietly simplify the test back into passing. Responsive work never buys
coverage by weakening the tests already there.

Running them:

| Command | Use |
| --- | --- |
| `npm run test:spec -- generated_tests/<feature>/<feature>.spec.ts` | While working on one family |
| `npm run test:local -- --suite <name> --mode headless` | One suite in its own isolated environment |
| `npm run test:sandboxed` | All suites, each isolated |

CI runs one GitHub job per spec file, each starting its own environment via
`lib/services/isolated_runner.ts` and invoking
`lib/services/run_playwright_tests.ts`. Running everything against one shared
instance is not supported — the suites mutate server state.

Three of the conventions above are enforced statically, because each fails
silently — the code looks right and the suite stays green:

| Guard | Enforces |
| --- | --- |
| `tests/yaffo/test_responsive_conventions.py` | Width breakpoints come from the documented set; a `vh` on a viewport-bound property has its `dvh` companion |
| `lib/services/touch_screenshot_policy.ts` | No fullPage capture before further assertions inside `withTouchContext` (trap 1) |
| `lib/services/test_timeout_policy.ts` | Specs inherit the runner's per-test budget |

The two TypeScript policies run when `playwright.config.ts` loads, so a
violation stops the suite before any test executes. Adding a breakpoint means
editing `ALLOWED_BREAKPOINTS` and this document in the same change — that is the
point of the guard, not an obstacle to it.

Visual review remains required for OpenLayers, GridStack, media and video
fitting, theme decoration, on-screen-keyboard behaviour and RTL. Geometry
assertions cannot establish that a screen reads correctly.

## Definition of done for a page

- No page-level horizontal overflow from 320px through desktop widths.
- No clipped, overlapped or unreachable controls at the target viewport sizes.
- Primary actions and state are equivalent across widths.
- Hover-only interactions have a coarse-pointer equivalent; drags have a
  touch-safe alternative.
- Scroll ownership is obvious and the document is the page scroller.
- Resizing and rotation preserve user state without a reload.
- English, German and Arabic pass the page's automated smoke checks.
- Every built-in theme passes visual review.
- The page's Playwright coverage asserts the minimum above, and ships in the
  same change as the page work.

## Traps

Each of these has cost a full debugging cycle.

1. **`page.screenshot({ fullPage: true })` permanently destroys Chromium's
   mobile emulation** on a context created with `isMobile: true`. Afterwards
   `(pointer: coarse)` and `(hover: none)` stop matching for the rest of that
   context and every coarse-pointer rule silently stops applying — a button
   measured 44px before the capture and 37px after, with no application change.
   Take viewport screenshots inside a touch context, or make the fullPage one
   the test's last action.
2. **A desktop browser never matches `(hover: none), (pointer: coarse)`**, so
   the touch sizing rules stay inert however narrow the window is. Target
   measurements taken by resizing a desktop window are meaningless; use a touch
   context.
3. **Chrome will not give you a viewport narrower than ~500px on macOS.** A
   "390px" window is really 500px and every rule below that goes untested.
   Driving the app inside a same-origin `<iframe width="390">` gives a true
   390px viewport with real media queries, and stays scriptable for
   measurement.
4. **Anything measured on the custom-pages canvas must wait for the band
   transition to finish.** The column class and `gs-*` attributes land
   immediately while the geometry is still travelling, and watching for two
   frames that round the same passes mid-transition. See `settleCanvas` in
   `generated_tests/custom_pages/custom_pages.spec.ts`.
5. **`modal.css` sets `.modal-content form { flex-direction: column }`** for the
   modal's own stacked forms, and it outranks a component's own rule. Any
   row-layout form placed in a modal meets this.

## Where things live

| Concern | Files |
| --- | --- |
| Shared responsive layer | `yaffo/static/responsive.css` |
| Shell and navigation | `yaffo/templates/base.html`, `yaffo/static/base.css`, `yaffo/static/nav.js`, `yaffo/static/pages/nav.css` |
| Panel registration | `yaffo/templates/components/nav_panel.html`, `yaffo/templates/_sidebar.html`, `yaffo/static/sidebar.css` |
| Shared components | `yaffo/static/components/`, `yaffo/static/form.css`, `yaffo/static/table.css`, `yaffo/static/button.css` |
| Themes | `yaffo/static/themes/` |
| Shared test assertions | `yaffo_ui_tests/generated_tests/_support/responsive.ts` |

Page-owned responsive CSS sits beside its page: `yaffo/static/index.css`,
`media/view.css`, `albums/albums.css`, `faces/index.css`, `locations/list.css`,
`sharing/sharing.css`, `settings/index.css`, `themes_page/index.css`,
`pages/detail.css`, `utilities/`, `error.css`.
