# Accessibility plan

Status: **Planned — no implementation started**

Last updated: **2026-08-30**

This plan covers keyboard operation, focus management, ARIA semantics, reading
order, contrast, and assistive-technology behaviour across every server-rendered
Yaffo page, shared component, built-in theme, and supported locale.

It was split out of `docs/development/responsive.md` on 2026-08-30. The two
concerns kept getting entangled — a focus trap and a dynamic viewport unit are
not the same kind of problem — and mixing them made both harder to finish and
harder to judge as done. Responsive work is judged by **geometry at a viewport**;
accessibility is judged by a **rule engine plus manual assistive-technology
review**. They meet only where a layout change moves a control out of reach.

Nothing here is implemented. Some of it was briefly built during the responsive
milestone and deliberately reverted so this workstream can do it once, properly,
with the tooling in place first.

## Why tooling comes first

The responsive branch's first pass at accessibility was hand-written
expectations: "focus starts inside the dialog", "Escape returns focus to the
opener", "nothing inside the closed panel is reachable by Tab". Those tests are
real, but they are the wrong shape as a foundation:

- they check the handful of things whoever wrote them happened to think of;
- they say nothing about the pages nobody wrote a test for;
- they encode one person's reading of a WCAG success criterion instead of a
  published rule with a stable id; and
- a failure points at a test, not at a criterion, so it is hard to triage or to
  argue about.

A rule engine inverts all four. Every route gets the same audit, findings carry
a rule id and an impact, and the checks that a machine genuinely cannot make
(does the focus order make sense, does the screen-reader announcement mean
anything) are left as an explicitly short manual list rather than being lost in
the noise. **Build the harness before writing assertions.**

## Phase A: Tooling and baseline

Nothing else in this plan starts until this phase is done.

1. **Adopt `@axe-core/playwright`** as the deterministic engine. It runs inside
   the Playwright suite that already exists, needs no separate server, and
   returns rule ids, impact levels, and the offending nodes. Configure it to
   WCAG 2.2 AA (`wcag2a`, `wcag2aa`, `wcag21a`, `wcag21aa`, `wcag22aa`) plus
   `best-practice`, with `best-practice` reported but not failing at first.
2. **Add an `_support/accessibility.ts` helper** beside the responsive one:
   `auditPage(page, options)` returning normalised violations, plus a snapshot
   comparison against a committed baseline so existing debt does not block the
   build while new debt does.
3. **Commit a baseline per route.** Audit every page family at a desktop and a
   narrow viewport, record what is already failing, and check the file in. The
   build fails on anything *new* or on any regression against a fixed entry.
   Without this the first run is a wall of findings nobody acts on.
4. **Decide the failure gate.** Proposal: `critical` and `serious` fail the
   build immediately; `moderate` and `minor` are tracked in the baseline and
   burned down per page family. Confirm with the product owner before Phase B.
5. **Add contrast checking against the theme tokens.** Contrast is the one axe
   rule that interacts badly with a themeable app: six built-in themes plus
   runtime AI-generated themes means the palette is not fixed at build time. A
   token-level check (every `--color-text-*` against every surface it is used
   on) catches this at the source, where a generated theme can also be validated
   before it is published.
6. **Consider a static pass over templates.** The Jinja templates are where most
   findings originate (missing labels, headings out of order, `div` used as a
   button). Evaluate `html-validate` with its a11y ruleset against rendered
   route output, as a fast pre-Playwright gate.

Deliverable: a green audit run with a committed baseline, plus a documented
command (`npm run test:a11y`) that any page owner can run.

## Phase B: Shared shell and components

Once the harness exists, the shared surfaces come first, because every page
inherits them.

1. **Focus management for disclosed UI.** The narrow-screen Menu and the peer
   page panels (`static/nav.js`, see the responsive plan's panel contract) open
   and close without moving focus today. Define and implement: focus entry on
   open, focus return to the invoking control on Escape and on outside
   dismissal, and no focus left stranded on a hidden node.
2. **One shared focus trap for dialog-like surfaces.** Three surfaces need
   identical behaviour and none has it: `components/modal.js`, the global
   confirm dialog (`components/confirm-dialog.js`), and the folder/file picker
   (`components/folder_picker.js`). Build one helper — focus the surface, cycle
   Tab within it, restore focus on release — and adopt it in all three rather
   than letting a fourth grow its own. A previous attempt at exactly this is in
   this branch's history if it is useful as a starting point.
3. **Audit the ARIA already in use.** `aria-expanded` on the nav toggles is
   load-bearing state today; confirm it is also *correct* semantics, and decide
   whether the panels want `role="region"` with a label, or something else.
4. **Native elements over reconstructed ones.** The selection checkbox is drawn
   as a CSS pseudo-element rather than an `<input>` (see
   `components/selection_bar.css`, which explains why); the same pattern appears
   in the faces grid. Decide whether these need a real control, an ARIA
   equivalent, or a documented exception.
5. **Hover-only content needs a keyboard path.** `[data-tooltip]` reveals on
   `:hover` and `:focus-visible`; the face source preview and the map's cluster
   hover behaviour have their own variants. WCAG 1.4.13 also wants such content
   dismissable and persistent.

6. **Reduced motion** is already handled globally in `static/responsive.css` and
   needs verification, not implementation. Ownership transferred from
   `responsive.md` on 2026-09-20, where it had been outstanding. Spot-checked
   during the responsive visual review and recorded as fine; what that pass did
   not single out is the GridStack band transition on custom pages, the most
   motion-heavy surface in the app. This serves users with vestibular
   disorders, for whom unwanted motion is a symptom trigger rather than a
   preference. Confirm under Phase D.
7. **Minimum target size** (WCAG 2.1 SC 2.5.8 Target Size (Minimum), Level AA —
   24×24 CSS px; SC 2.5.5 Target Size, AAA, asks 44×44). Transferred from
   `responsive.md` on 2026-09-20. The responsive work already ships 44 px
   targets across the shell, pagination, album reorder controls, the standalone
   error/CSRF screens and the narrow menu, so this is very likely a pass in
   practice; what is missing is a criterion that owns it and a check that proves
   it. The sizing rule itself lives in `static/responsive.css` under
   `@media (hover: none), (pointer: coarse)`.

   **How to test it is an open question — deliberately recorded rather than
   guessed.** What is known so far:

   - A desktop browser **never matches** `(hover: none), (pointer: coarse)`, so
     the 44 px floor stays inert no matter how narrow the window is. Measuring
     targets by resizing a window produces meaningless numbers — a sweep at
     390px reported 13 undersized controls on `/settings` and 3 on `/people`
     that the coarse-pointer rule fixes on a real device.
   - Playwright *can* reach it: a context created with `isMobile: true` /
     `hasTouch` matches the query, and `generated_tests/_support/responsive.ts`
     already exposes `withTouchContext` for exactly this. Several specs assert
     `boundingBox().height >= 44` inside it today.
   - **Trap:** one `page.screenshot({fullPage: true})` permanently tears down
     that emulation for the rest of the context — after it, the same button
     measured 44 px then 37 px with no application change. Any target-size
     assertion must run before any full-page capture.
   - axe-core has a `target-size` rule, but it is not in the default ruleset in
     every version; whether to lean on it or keep hand-written geometry
     assertions is part of the open question.
   - Decide which level is being claimed. The app builds to 44 px (AAA's
     number) while AA only requires 24 px, so the bar being asserted should be
     stated rather than inferred from whatever the CSS happens to do.


## Phase C: Page families

Run per family, in the same P1–P8 division the responsive plan uses, so the same
owner can pick up both if that is convenient:

| Family | Known accessibility work |
| --- | --- |
| Library, timeline, media detail | Timeline scrubber keyboard alternative; grid roving focus; video player controls |
| Albums | Selection-mode semantics; drag-reorder keyboard path (direct controls exist, confirm they are announced); modal labelling |
| Faces and people | The numeric assignment shortcuts and their discoverability; face grid selection semantics; the people card presentation's reading order |
| Locations | Map keyboard operation, or a documented non-map equivalent for every task the map affords; cluster selection announcement |
| Utilities and automations | Live-region announcements for scan/job progress; code editor and trigger builder keyboard operation |
| Sharing | Pairing-code entry, transfer progress announcements, device list semantics |
| Settings and themes | Form labelling, error association, destructive-action confirmation; theme preview contrast |
| Custom pages and widgets | GridStack design-mode keyboard operation; generated widget HTML must be required to be accessible, not just responsive |

## Phase D: Manual review

The part a rule engine cannot do, kept deliberately small so it actually gets
done:

1. Keyboard-only walkthrough of each primary task, no mouse.
2. Screen-reader pass (VoiceOver on macOS, NVDA on Windows) over the shell, one
   gallery, one form-heavy page, and one dialog.
3. Reading-order review where the visual order and DOM order diverge — most
   likely the narrow-screen panel host, which moves live DOM into the navbar.

   **Known divergence, recorded 2026-09-20 — media detail, faces section.**
   The panel host turns out not to be a divergence at all: `nav.js` *moves* the
   live node rather than cloning it, so its visual and DOM order stay in step.
   This is the first real one. Below 900px `.detail-section-faces` is pulled
   above the rest of the metadata with `order: -1`
   (`yaffo/static/media/view.css`), because tapping a face highlights a region
   on the photo and in source order the faces sat 942px below it — the photo
   had scrolled off the top by the time you could reach them. `order` moves
   paint, not traversal, so a keyboard user now sees Faces first and tabs into
   File Information first. That fails "Focus order follows the visual and
   reading order" below, knowingly.

   Three ways to close it, none taken yet: reorder the template so the DOM
   matches the narrow order and re-order for desktop instead (which moves the
   mismatch rather than removing it); relocate the node the way `nav.js`
   relocates panels, which removes it in both modes at the cost of JS; or
   `reading-flow`, once it is not Chromium-only.
4. A pass in a long locale (German) and an RTL locale (Arabic), since both
   change announced content and traversal order.
5. **200% text zoom** (WCAG 2.1 SC 1.4.4, Level AA). Transferred from
   `responsive.md` on 2026-09-20, where it had been outstanding work blocking a
   milestone it does not belong to.

   Test **text-only** zoom, not page zoom. Page zoom scales CSS pixels, so a
   1280px viewport at 200% behaves like a 640px one and the responsive
   breakpoints already serve it. Text-only zoom — Firefox's text-only mode, OS
   font scaling, a user stylesheet — grows text inside boxes that do not grow
   with it, and that is the failure SC 1.4.4 is written against. The audience is
   not niche: low vision, presbyopia (most people past about 45), and anyone who
   simply runs their browser large.

   Most likely to break, from the responsive review: the three-control navbar
   row at ≤400px, pagination icon rows, table headers, and chip lists. Anything
   with a fixed `height`, a `min-height` the text can outgrow, or a single-line
   truncation is a candidate.

   **Open scope question.** The responsive checklist records this as "Not
   supported". That is a decision, not a result, and it needs an explicit one
   here: either it is in scope and these screens get fixed, or it is recorded as
   out of scope with a reason. It should not stay implicit.
6. **Reduced motion**, verifying the global rule recorded in Phase B item 6 —
   every animated surface, and the custom-pages band transition first.

## Definition of done for each page

- No new axe violations at `critical` or `serious` against the committed
  baseline, at both a desktop and a narrow viewport.
- Every task achievable with a pointer is achievable with the keyboard alone.
- Disclosed UI moves focus in on open and returns it on close.
- Dialog-like surfaces trap focus and are dismissable with Escape.
- Every control has an accessible name; every form control has an associated
  label; error messages are programmatically associated with their input.
- Hover-revealed content is also reachable by focus, is dismissable, and
  persists while pointed at.
- Focus order follows the visual and reading order.
- Interactive targets meet the minimum size this plan claims (see Phase B item
  7), measured in a touch context rather than a resized desktop window.
- Contrast passes against every built-in theme's tokens.
- The manual checklist above is run for the page family and its findings are
  either fixed or recorded in the baseline with a reason.

## Relationship to the responsive plan

`docs/development/responsive.md` owns viewport behaviour: overflow, breakpoints,
touch targets, coarse-pointer equivalents, dynamic viewport units, safe areas,
state preservation across resize. Where the two meet — a control that a layout
change pushed off-screen, or a panel that is visually hidden but still in the
tab order — the responsive plan owns the geometry and this plan owns the
traversal. Neither plan should grow assertions belonging to the other.

**Transferred in on 2026-09-20:** 200% text zoom, `prefers-reduced-motion`, and
minimum target size — all three were outstanding work in `responsive.md`. Each
presents as a layout question, which is how they ended up there, but each is a
WCAG criterion whose users are people with a specific need rather than people on
a specific device, and none was blocking anything the responsive milestone
actually owns. Their verification is this plan's, and will be done on its own
branch.

The seam with target size is worth stating, because the responsive plan still
carries most of the coarse-pointer work: **that** plan owns whether a tap path
exists at all — a touch equivalent for hover-only content, an alternative to any
drag — and **this** one owns whether the resulting target is big enough. The
44 px controls already shipped are recorded there as history; the criterion and
its check are here. Safe-area insets and text direction stay there too, being
device and locale properties rather than assistive ones.
