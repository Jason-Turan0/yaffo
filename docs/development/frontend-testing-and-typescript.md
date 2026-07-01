# Frontend JavaScript: Unit Testing & TypeScript Proposal

Status: **Proposal (with decisions recorded — §0)** · Author: analysis pass · Scope: `yaffo/static/**/*.js`

This document analyzes the browser-side JavaScript in Yaffo, proposes a pragmatic
way to introduce **unit tests**, and assesses the **feasibility of migrating to
TypeScript**. It is intentionally incremental — nothing here requires a big-bang
rewrite or a new build step on day one.

---

## 0. Decisions

Settled choices (the rest of the doc is the supporting analysis). Date: 2026-06-25.

| # | Decision | Rationale | Detail |
|---|----------|-----------|--------|
| D1 | **Types: JSDoc + `checkJs`, type-check only — no build step.** Author types as JSDoc on the existing `.js`; run `tsc --noEmit`. Same files ship. | Preserves the zero-build, served-verbatim model (clean PyInstaller packaging); ~80% of TS's day-to-day benefit at ~5% of the cost; fully reversible. | §4.2 Option 1, §4.3 |
| D2 | **JSDoc comments ship in `dist` as-is (inert).** No stripping. | Stripping *is* a build step, which would forfeit D1's whole point. Comments are inert and tiny next to vendored bundles. | §4.5 note |
| D3 | **Full TypeScript (`.ts` + bundler) is deferred, not rejected.** Revisit only if JSDoc verbosity (e.g. index signatures) becomes the bottleneck. `allowJs` keeps the path open for gradual `.js`→`.ts`. | A mandatory build touches dev loop + CI + packaging; not worth it yet. | §4.2 Option 2 |
| D4 | **Unit tests run from the CLI via npm scripts; coverage is included** (`vitest run --coverage`, v8 provider, scoped to `yaffo/static/**` excluding `vendor/`). | Mirrors the existing `yaffo_ui_tests` npm-script workflow; coverage is first-class, no extra framework. | §3.7 |
| D5 | **DOM fixtures come from the real Jinja templates, not hand-written HTML** — rendered to committed fixture files by a generation step, drift-guarded in CI. | Keeps the template as the single source of truth; prevents test/markup duplication. | §3.8 |
| D6 | **Page modules use page-level namespaces and explicit app initialization.** Page APIs live under `window.PHOTO_ORGANIZER.<pageName>`, and templates/page scripts wait for `yaffo:app-init-complete` instead of calling `i18nReady` directly. | Keeps the root namespace from becoming a flat list of unrelated page functions, and gives every page the same ready app object (`i18n`, `APP_CONFIG`, shared components). | §4.5–§4.6 |
| D7 | **App-level components initialize in the root app initializer.** Shared components under `window.PHOTO_ORGANIZER.COMPONENTS` are initialized by `static/app.js` before `yaffo:app-init-complete` is dispatched. | Consuming pages should not need to know app-wide component boot order or repeat global component initialization details. | §4.5–§4.6 |

Current implementation: the frontend unit-test and type-check tooling lives at
the repo root. `package.json` drives Vitest/jsdom unit tests and `tsc --noEmit`
checks the migrated JavaScript files listed in `tsconfig.json`.

---

## 1. Current JavaScript landscape

### 1.1 Inventory

~3,000 lines of first-party JS across **37 files** (vendored libraries excluded):

| Area | Files | Notable modules |
|------|-------|-----------------|
| Page modules | `utilities/`, `settings/`, `pages/`, `people/`, `locations/`, `faces/`, `themes_page/`, `media/`, `filters/` | `pages/grid.js` (434), `locations/list.js` (581), `faces/index.js` (416), `utilities/automations.js` (367) |
| Shared components | `components/` | `cron_builder.js` (288), `chat_dialog.js`, `modal.js`, `overlay.js`, `folder_picker.js`, `confirm-dialog.js` |
| Global services | root | `searchable-select.js` (326), `settings/index.js` (264), `i18n.js`, `notification.js`, `utils.js`, `multi-select.js` |
| Vendored (excluded) | `vendor/` | htmx 2.0.4, OpenLayers 10.3.1, gridstack 10.3.1, i18next 25.7.4 |

Loading model: plain `<script src>` tags in `templates/base.html` plus a
per-page `{% block scripts %}`. **No bundler, no transpile, no `package.json`** in
the app itself — files are served verbatim from `yaffo/static/`. Modern syntax is
used directly (arrow functions, `async/await`, `fetch`, template literals,
`TextDecoder`/streams, `Intl`).

### 1.2 The dominant pattern is already test-friendly

35 of 37 files follow the convention documented in `CLAUDE.md`: a namespaced
factory attached to a single global, taking its dependencies as parameters and
returning a public API. From `utilities/index_photos.js`:

```js
window.PHOTO_ORGANIZER = window.PHOTO_ORGANIZER || {};
window.PHOTO_ORGANIZER.initIndexPhotos = (opts, i18n, config) => {
    // ... closure-scoped helpers ...
    return { runScan, startSync };   // public API, ideal seam for tests
};
```

This is close to ideal for unit testing:

- **Dependency injection** — `i18n` and `config` (and `opts`) arrive as
  arguments, so a test passes fakes instead of standing up i18next or Flask.
- **Pure-ish helpers in closure scope** — e.g. `index_photos.js`'s record
  handling, `utils.js` date formatting, `multi-select.js` label formatting.
- **Returned public API** — `{ runScan, startSync }` gives tests a handle without
  reaching into internals.

### 1.3 What stands in the way of testing today

1. **No module exports.** Files attach to `window.PHOTO_ORGANIZER` as a side
   effect of evaluation; there is no `export`. A test runner can't `import` them —
   it must evaluate the file against a global and read the namespace back off it.
2. **DOM coupling.** Most modules call `document.getElementById` / `createElement`
   directly, so tests need a DOM (`jsdom`), not pure Node.
3. **Implicit globals.** Modules assume `window.notification`,
   `window.APP_CONFIG`, `window.i18next`, and `window.PHOTO_ORGANIZER.i18n` exist
   (set up elsewhere in `base.html`). Tests must provide these.
4. **One legacy outlier.** `multi-select.js` uses bare top-level
   `function` declarations wired through inline `onclick=` attributes — it does
   not follow the factory pattern and is the least testable file.

None of these are blockers; they shape the harness choice (§3).

### 1.4 Existing tooling we can build on

`yaffo_ui_tests/` is a **git-tracked, already-installed** AI-augmented
**Playwright + MCP** end-to-end framework (TypeScript 5.7, Jest 30 via `ts-jest`
ESM, ESLint 9, `tsx`). Important nuances:

- It tests the app **through a real browser** (`generated_tests/**/*.spec.ts`) and
  contains unit tests **of its own `lib/` framework** (`jest` `testMatch:
  **/__tests__/**/*.test.ts`, `testEnvironment: node`).
- It does **not** unit-test any code in `yaffo/static/`. There is currently **zero
  unit-test coverage of first-party frontend logic.**

So the toolchain (Node, TS, Jest, ESLint config) is already proven in-repo — we
are adding a *new target* (the app's browser modules), not bootstrapping Node
tooling from scratch.

### 1.5 Backend reference points (for parity)

Python is tested with `pytest` + `pytest-cov` and type-checked with `mypy`, under
`tests/` mirroring the package tree. The frontend proposal below mirrors the
backend's "mirror-the-tree + a CI gate" shape. A check-in workflow
(`.github/workflows/checks.yml`) now runs the JS type-check + unit tests and the
Python unit tests (`pytest -m unit`) on pushes to `master` and on pull requests —
the first test CI gate in the repo (release stays in its own `release.yml`).

---

## 2. Goals

1. Make the **logic-heavy, bug-prone** frontend modules unit-testable and tested:
   NDJSON stream parsing (`index_photos.js`), date/relative-time formatting
   (`utils.js`), i18n service shape (`i18n.js`), multi-select label formatting,
   cron builder, searchable-select filtering.
2. Keep the **zero-build, served-verbatim** model for production for as long as
   possible — testing must not force a bundler on the app.
3. Lay a path to **type safety** that can start *today* without a build step and
   graduate to full TypeScript only if/when the team wants it.
4. Add a **CI gate** so regressions are caught.
5. Refactor frontend initialization so page code uses explicit page-level
   namespaces and the global app-ready event instead of scattered
   `i18nReady.then(...)` chains.

Non-goals: replacing the Playwright E2E suite (complementary — E2E covers
integration/DOM-in-browser; units cover logic fast and deterministically), and
testing vendored libraries.

---

## 3. Part A — Unit testing proposal

### 3.1 Recommended harness: Vitest + jsdom

**Recommendation: add a small `vitest` setup at repo root (or under a new
`tests_js/`), using the `jsdom` environment.**

Why Vitest over reusing the `yaffo_ui_tests` Jest:

| Factor | Vitest + jsdom | Reuse `yaffo_ui_tests` Jest |
|--------|----------------|------------------------------|
| DOM env | `jsdom` first-class, one-line config | Jest `testEnvironment` is `node`; needs `jest-environment-jsdom` added |
| Loading raw `window.*` global modules | trivial — `import './file.js'` runs side effects, or `vi.stubGlobal` | works, but ESM/`ts-jest` config is tuned for the framework's `.ts`, not the app's plain `.js` |
| Speed / DX | fast watch mode, esbuild-based | fine, heavier config |
| Coupling | app tests independent of the E2E framework's lifecycle | mixes two very different test targets in one package |

Either works; Vitest is the lower-friction choice for plain browser `.js`. If the
team prefers a single Node toolchain, Jest + `jest-environment-jsdom` in
`yaffo_ui_tests` is an acceptable alternative — the test *authoring* below is
nearly identical.

### 3.2 The loading shim (the one real piece of glue)

Because modules attach to a global instead of exporting, a tiny helper imports a
static file against the current jsdom globals and hands back the namespace:

```js
import path from 'node:path';
import { pathToFileURL } from 'node:url';

const STATIC_DIR = path.join(process.cwd(), 'yaffo', 'static');
let reloadCounter = 0;

/**
 * Load a yaffo/static module into the current jsdom global.
 *
 * The app's modules attach to `window.PHOTO_ORGANIZER` as a side effect of
 * evaluation. Importing through Vitest keeps coverage mapped to the real file,
 * and a cache-busting query gives each test a fresh module evaluation.
 */
export async function loadModule(relPath) {
  const fileUrl = pathToFileURL(path.join(STATIC_DIR, relPath)).href;
  await import(/* @vite-ignore */ `${fileUrl}?reload=${reloadCounter++}`);
  return window.PHOTO_ORGANIZER;
}
```

### 3.3 Fakes for the implicit globals

A shared setup provides the three things modules assume exist:

- `window.APP_CONFIG` — `{ urls: {...}, buildUrl, i18n: { locale: 'en' } }`
- `window.notification` — `{ success: vi.fn(), error: vi.fn(), ... }`
- an `i18n` fake — `{ t: (k, o) => k, number: (n) => String(n), ... }` (modules
  already receive this as a *parameter*, so most tests inject it directly).

### 3.4 Worked example

```js
// tests_js/utilities/index_photos.test.js
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { loadModule } from '../support/load_module.js';

describe('initIndexPhotos.handleRecord', () => {
  beforeEach(() => {
    document.body.innerHTML = `
      <span id="stat-total-filesystem"></span>
      <button id="sync-button" hidden></button>
      <div id="scan-results"></div>`;
    window.notification = { success: vi.fn(), error: vi.fn() };
  });

  it('updates the filesystem counter on progress records', () => {
    const PO = loadModule('utilities/index_photos.js');
    const i18n = { t: (k) => k, number: (n) => `#${n}` };
    const api = PO.initIndexPhotos(
      { canScan: false, canSync: true, hasActiveJobs: false, mediaDirs: [] },
      i18n,
      { urls: {} },
    );
    // drive the public surface / dispatch a record (expose handleRecord or call runScan with a stubbed fetch)
    expect(document.getElementById('stat-total-filesystem').textContent).toBe('');
  });
});
```

The richest near-term target is `index_photos.js`'s `handleRecord` /
`runScan` NDJSON loop — pure data-in, DOM-out logic with three record types and a
buffer-splitting reader that is exactly the kind of thing that breaks silently.
(Consider returning `handleRecord` from the factory's public API to test it
directly.)

### 3.5 Phased rollout

**Phase 0 — harness (½ day).** Add `tests_js/` + `vitest.config.js` + the loading
shim + shared fakes. One smoke test green.

**Phase 1 — pure logic, highest ROI (1–2 days).** Target modules with real branch
logic and minimal DOM:
- `utils.js` — `date.format`, `formatWithTime`, `formatRelative` (boundary cases:
  empty, invalid, the day/hour/minute/30-day cutoffs).
- `index_photos.js` — record handling + the NDJSON buffer split (partial lines,
  trailing record without newline).
- `multi-select.js` — `updateMultiSelectText` formatting (0/1/N selected,
  custom formats) and `filterMultiSelectOptions`. *Refactor this file into the
  factory pattern first* (see §3.6) — it pays for itself here.
- `i18n.js` — the returned service shape (`number`/`percent`/`date`/`list`
  delegate to `Intl` with the right locale).

**Phase 2 — components (2–4 days).** `cron_builder.js`, `searchable-select.js`,
`modal.js`, `folder_picker.js` — DOM-building components testable with jsdom
fixtures.

**Phase 3 — page modules, as touched.** Add tests opportunistically when editing
`pages/grid.js`, `locations/list.js`, `faces/index.js`, `automations.js`. Don't
backfill all at once.

### 3.6 Small refactors that unlock testing

- **Convert `multi-select.js`** to the `initMultiSelect(...)` factory and drop the
  inline `onclick=` handlers (replace with `addEventListener` wiring). Brings the
  last outlier in line with `CLAUDE.md` and makes it testable.
- **Move page APIs under page-level namespaces.** For example,
  `utilities/automations.js` exposes `window.PHOTO_ORGANIZER.automations.*`
  instead of adding `initAutomation*` functions directly to
  `window.PHOTO_ORGANIZER`.
- **Use the global app-ready event for page entrypoints.** Templates should
  listen for `yaffo:app-init-complete`, read `event.detail.app`, and pass
  `app.i18n`, `window.APP_CONFIG`, and initialized components into page functions
  explicitly.
- **Expose pure helpers** on the returned API where a test wants them (e.g.
  `handleRecord` in `index_photos.js`) rather than reaching into closures.
- Keep DOM lookups behind the existing `el()`/`getElementById` helpers so fixtures
  stay simple.

### 3.7 Running the tests (CLI) & coverage

Vitest is a **CLI tool driven through npm scripts** — the same workflow the
existing `yaffo_ui_tests` already uses for Jest/Playwright. `package.json`:

```json
{
  "scripts": {
    "test:unit": "vitest run",
    "test:unit:watch": "vitest",
    "test:unit:cov": "vitest run --coverage"
  }
}
```

| Command | Purpose |
|---|---|
| `npm run test:unit` | one-shot, exits non-zero on failure — **the CI form** |
| `npm run test:unit:watch` (or bare `npx vitest`) | watch mode, re-runs only affected tests — the dev loop |
| `npx vitest run path/to/file.test.js` | a single file |
| `npx vitest run -t "handleRecord"` | tests matching a name |
| `npx vitest --ui` | optional browser results UI |

Gotcha: bare `vitest` defaults to **watch**; CI must use `vitest run` or it hangs
waiting for file changes.

**Coverage is first-class** — no extra framework, just one dev dependency
(`@vitest/coverage-v8`, the fast V8 provider; `istanbul` is the alternative) and
the `--coverage` flag. Config in `vitest.config.js`:

```js
import { defineConfig } from 'vitest/config';

export default defineConfig({
  test: {
    environment: 'jsdom',
    setupFiles: ['./tests_js/support/setup.js'],   // the global fakes from §3.3
    coverage: {
      provider: 'v8',
      include: ['yaffo/static/**/*.js'],
      exclude: ['yaffo/static/vendor/**'],           // skip htmx/OpenLayers/gridstack/i18next
      reporter: ['text', 'html', 'lcov'],            // terminal table + browsable HTML + lcov for CI
      // thresholds: { lines: 60 },                   // add after Phase 1 (see below)
    },
  },
});
```

That yields a per-run terminal table, a browsable `coverage/index.html`, and an
`lcov` file CI can ingest.

**CI gate.** Add a `frontend` job to a (new) GitHub Actions workflow: `npm ci` +
`npm run test:unit:cov`, gating PRs. Start with **no threshold**; once Phase 1
lands, set a floor (e.g. `thresholds: { lines: 60 }`) **scoped to the tested
files, not the whole tree**, so it fails the build if coverage drops rather than
reporting a vanity number. This is the first JS gate in CI — pair it with `eslint`
(config already exists in `yaffo_ui_tests`) and `tsc --noEmit` (D1).

### 3.8 Test fixtures vs. Flask templates — avoiding drift

A unit test needs DOM to operate on. Hand-writing that HTML in each test
**duplicates the Jinja markup** and silently rots when a template changes. Two
facts make this manageable here:

**Most modules build their own DOM — minimal fixtures, low drift risk.** Modules
like `index_photos.js` create tables via `document.createElement`; the server only
provides empty containers (`#scan-results`, `#sync-button`, the stat `<span>`s).
The fixture is just those few hollow elements — there's almost nothing to drift.
Keep these fixtures tiny and inline.

**The drift risk is real only for *enhancement* modules** — ones that query
server-rendered markup (`multi-select.js` reading `.multi-select-wrapper[data-*]`,
`searchable-select.js`, component macros). For these, **render the actual Jinja
fragment and use its output as the fixture** so the template stays the single
source of truth:

- **Yes, Flask templates can render test components.** A small pytest renders the
  real macro/fragment in isolation (Jinja can render a single
  `{% macro %}` / `{% include %}` without a full page) and writes the HTML to a
  committed fixtures dir the JS tests load via jsdom:

  ```python
  # tests/yaffo/frontend_fixtures/test_render_fixtures.py
  def test_render_multi_select_fixture(app):
      html = render_template("components/multi_select.html", options=SAMPLE)
      (FIXTURES / "multi_select.html").write_text(html)
  ```
  ```js
  // tests_js/multi-select.test.js
  document.body.innerHTML = readFileSync('tests_js/fixtures/multi_select.html', 'utf8');
  ```

- **Drift guard:** run that renderer in CI and `git diff --exit-code` the fixtures
  dir — if a template changes without the fixture being regenerated, CI fails.
  Same shape as the backend's existing schema drift-guard tests.

- **Don't reimplement Jinja in JS.** Jinja is Python; JS look-alikes (nunjucks)
  are *not* faithful to its macro/filter semantics. Render with real Jinja
  (option above) or keep the fixture hollow — never maintain a parallel JS
  template.

Rule of thumb: **generate DOM in JS → hollow inline fixture; enhance server DOM →
rendered-from-template fixture with a CI drift guard.** This is decision **D5**.

---

## 4. Part B — TypeScript feasibility

### 4.1 The core tension

The app ships JS **without a build step** — `yaffo/static/*.js` is served as-is and
bundled into the PyInstaller app verbatim (see `docs/development/distribution.md` /
packaging notes). Full TypeScript requires a **compile step** that emits the JS
Flask serves, which touches dev workflow, the static-file pipeline, and packaging.
That cost — not the language — is the real decision.

The good news: the codebase is **already structured like typed code** (small
factories, explicit parameter objects, JSDoc in places like `utils.js`), and the
TS toolchain already exists in `yaffo_ui_tests`. So the *language* migration is
low-risk; the *build/packaging* integration is the part to be deliberate about.

### 4.2 Three options

**Option 1 — JSDoc + `checkJs`, type-check only (no build). ⭐ Recommended first.**

Add a root `tsconfig.json` with `allowJs: true`, `checkJs: true`,
`noEmit: true`, and type the existing `.js` via JSDoc comments (already started in
`utils.js`). Run `tsc --noEmit` in CI for type errors. **Zero runtime change** —
the same `.js` ships; nothing is transpiled or bundled.

- *Pros:* immediate type safety on the real files, no build step, no packaging
  impact, reversible, incremental file-by-file (`// @ts-check` per file or global).
  Editors get autocomplete/inline errors today.
- *Cons:* JSDoc is more verbose than TS syntax; no enums/interfaces-as-syntax
  (use `@typedef`). Can't use TS-only features.
- *Effort:* ½ day setup; ongoing per-file typing. Pairs perfectly with the test
  rollout — type the file when you write its first test.

**Option 2 — Full TypeScript with a build step.**

Author `.ts` under `yaffo/static_src/`, compile with `esbuild`/`tsc` to
`yaffo/static/` (or a `dist/`), and serve the output.

- *Pros:* full TS ergonomics (interfaces, enums, stricter inference), shareable
  types with `yaffo_ui_tests`.
- *Cons:* introduces a **mandatory build** for every static change (dev watch +
  CI + **PyInstaller packaging must run it** before bundling); source maps needed
  for debugging; a new failure mode (stale build). Meaningful workflow change for a
  solo/small project that currently has none.
- *Effort:* 2–3 days to wire build + packaging + source maps, then incremental
  per-file conversion. Best done **module-by-module** since `allowJs` lets `.ts`
  and `.js` coexist during migration.

**Option 3 — Status quo (plain JS, no types).** Keep as-is; rely on tests only.
Lowest effort, no type safety. Viable if the team doesn't value static types.

### 4.3 Recommendation → Decision D1/D3

**Adopt Option 1 now; treat Option 2 as an optional later graduation.** (Recorded
as **D1** and **D3** in §0.)

Rationale: the no-build-step property is genuinely valuable here (simple dev loop,
clean PyInstaller packaging — see the packaging notes about the fragility of the
build/launch glue). `checkJs` + JSDoc captures ~80% of TypeScript's day-to-day
benefit (catching shape/null/typo bugs, editor intel) at ~5% of the integration
cost and with full reversibility. If, after living with typed JSDoc, the verbosity
becomes the bottleneck, Option 2 is a clean follow-on because `allowJs` permits a
gradual `.js`→`.ts` migration with both coexisting.

### 4.4 Migration mechanics (whichever option)

- The `init*(opts, i18n, config)` factories map cleanly to typed signatures:
  define `interface AppConfig`, `interface I18nService`, and per-page `opts`
  types once (shareable with `yaffo_ui_tests`).
- The `window.PHOTO_ORGANIZER` global wants an ambient declaration
  (`declare global { interface Window { PHOTO_ORGANIZER: ... } }`) so every file
  sees a typed namespace.
- Start with leaf utilities (`utils.js`, `i18n.js`) — few dependencies, high reuse
  — then components, then pages, same order as the test rollout. Type a file when
  you add its first test.

### 4.5 Golden example — `components/cron_builder.js`

Use `cron_builder.js` and `utilities/automations.js` as the reference migration
shape for DOM components and page modules. They show the intended split between
runtime construction, page-level namespacing, explicit initialization through the
global app-ready event, dependency injection, and JSDoc type checking without a
build step.

The public API has two factory methods:

```js
/**
 * @param {CronBuilderDeps} deps
 * @returns {CronBuilderApi}
 */
cronBuilderWindow.PHOTO_ORGANIZER.COMPONENTS.createCronBuilder = ({ i18n, document: cronDocument = document }) => {
    // Build a runtime instance. No global async work, no DOM auto-init.
    return { initAll, describeCron, reset, setCron };
};

/**
 * @param {CronBuilderDeps} deps
 * @returns {CronBuilderApi}
 */
cronBuilderWindow.PHOTO_ORGANIZER.COMPONENTS.initCronBuilder = (deps) => {
    const cronBuilder = cronBuilderWindow.PHOTO_ORGANIZER.COMPONENTS.createCronBuilder(deps);
    const cronDocument = deps.document || document;
    cronBuilderWindow.PHOTO_ORGANIZER.COMPONENTS.cronBuilder = cronBuilder;
    cronBuilder.initAll(cronDocument);
    cronDocument.body.addEventListener("htmx:afterSwap", (event) => {
        cronBuilder.initAll(/** @type {Element} */ (event.target));
    });
    return cronBuilder;
};
```

The app initializer owns the async boundary. Page scripts listen for
`yaffo:app-init-complete`, take the completed app object from `event.detail.app`,
and initialize only page-owned behavior. App-level components under
`window.PHOTO_ORGANIZER.COMPONENTS` are initialized by `static/app.js` before the
event is dispatched, so consuming pages should treat `app.COMPONENTS` as ready
and should not repeat global component bootstrapping.

```html
<script>
    document.addEventListener('yaffo:app-init-complete', (event) => {
        const app = event.detail.app;
        app.automations.initAutomationTest(
            {{ selected_slug | tojson }},
            window.APP_CONFIG,
            {{ default_media_dir | tojson }},
            app.i18n
        );
    });
</script>
```

Page modules should install their API under a page-level namespace:

```js
window.PHOTO_ORGANIZER = window.PHOTO_ORGANIZER || {};
window.PHOTO_ORGANIZER.automations = window.PHOTO_ORGANIZER.automations || {};

const automations = window.PHOTO_ORGANIZER.automations;

automations.initTriggerEditor = (i18n, cronBuilder) => {
    // page behavior
};
```

Key points to copy:

- **Use `create*` for runtime instances.** It receives dependencies and returns an
  API object. It should not wait on `i18nReady`, attach global event listeners, or
  mutate the shared namespace beyond installing the factory itself.
- **Use `init*` for the shared page component.** It calls `create*`, stores the
  shared instance on `window.PHOTO_ORGANIZER.COMPONENTS`, performs initial DOM
  setup, and wires HTMX re-initialization.
- **Initialize app-level components in `static/app.js`.** Global/shared
  components should be wired by the root app initializer before
  `yaffo:app-init-complete` fires. Pages consume `app.COMPONENTS`; they do not
  call global `initAll()` routines or recreate app-wide component instances.
- **Keep async orchestration in `static/app.js`.** Page templates and page
  entrypoints consume `yaffo:app-init-complete`; they should not call
  `window.PHOTO_ORGANIZER.i18nReady.then(...)` directly for page setup.
- **Use page-level namespaces for page modules.** Put page-specific entrypoints
  under `window.PHOTO_ORGANIZER.<pageName>`, such as
  `window.PHOTO_ORGANIZER.automations.initAutomationTest`, rather than placing a
  flat list of functions on `window.PHOTO_ORGANIZER`.
- **Inject dependencies into consumers.** `initTriggerEditor(i18n, cronBuilder)`
  takes the component it uses instead of awaiting a global `cronBuilderReady`
  promise inside click handlers.
- **Use file-specific window aliases.** `cronBuilderWindow` avoids top-level
  lexical collisions between classic scripts. Do not reuse a generic
  `const appWindow = ...` name across multiple static files.
- **Type public contracts first.** `CronBuilderDeps`, `CronBuilderApi`, and the
  component root/control types make the public surface readable while keeping DOM
  casts localized near `querySelector`.
- **Back behavior with focused unit tests.** The cron tests assert the factory is
  synchronous, `initCronBuilder` stores and initializes the shared instance, and
  `automations.js` uses the injected builder when opening schedules.

### 4.6 Migration checklist

Use this checklist when migrating another frontend file to unit tests and
`checkJs`.

- [ ] Identify the public entrypoint: `initPageFeature(...)`,
  `createComponent(...)`, or `initComponent(...)`.
- [ ] Keep runtime factories synchronous. Do not add hidden
  `i18nReady.then(...)` calls inside component modules.
- [ ] Move page orchestration to the app-ready event: listen for
  `yaffo:app-init-complete`, read `event.detail.app`, create/init page-owned
  dependencies, then pass them into consumers.
- [ ] If a component is app-level/shared, initialize it in `static/app.js` before
  dispatching `yaffo:app-init-complete`; pages should consume the ready
  `app.COMPONENTS` entry instead of bootstrapping it.
- [ ] Put page-level APIs under `window.PHOTO_ORGANIZER.<pageName>` instead of
  adding new root-level `window.PHOTO_ORGANIZER.init*` functions.
- [ ] Do not add new page-level `window.PHOTO_ORGANIZER.i18nReady.then(...)`
  chains. Use `app.i18n` from the app-init event.
- [ ] Prefer two factory methods for shared DOM components:
  `createThing(deps)` returns an instance, and `initThing(deps)` creates, stores,
  initializes, and wires page-level listeners.
- [ ] Replace promise globals such as `thingReady` with explicit injected
  dependencies where practical.
- [ ] Use a file-specific typed window alias, e.g. `cronBuilderWindow`, not a
  repeated top-level `appWindow`.
- [ ] Add `// @ts-check` to the migrated file.
- [ ] Add named JSDoc typedefs for injected dependencies, public API return
  shape, important DOM roots, and non-trivial data records.
- [ ] Keep casts close to DOM boundaries (`querySelector`, `event.target`,
  `dataset`, `value`), not scattered through business logic.
- [ ] Convert nullable DOM lookups deliberately: either guard missing elements or
  cast only when the component just rendered the markup itself.
- [ ] Add the migrated file to `tsconfig.json`'s `files` list.
- [ ] Add or update unit tests in `tests_js/`, using `tests_js/support/setup.js`
  for globals and `tests_js/support/load_module.js` to load static modules.
- [ ] Test the new dependency boundary: factory is synchronous, initializer stores
  shared state when expected, and consumers receive dependencies as arguments.
- [ ] Run `npm run typecheck:js`.
- [ ] Run `npm run test:unit`.

---

## 5. Recommended roadmap

| Step | Effort | Outcome |
|------|--------|---------|
| 1. Vitest + jsdom harness, loading shim, fakes (§3.2–3.3) | Done | root unit-test harness is in place |
| 2. Root `tsconfig.json`, `checkJs`/`noEmit`, type initial files (Option 1) | Done | type-checking with no build |
| 3. Migrate shared/global components with the cron builder pattern (§4.5–4.6) | ongoing | tested, typed components with explicit DI |
| 4. Migrate app/page initialization to root-initialized components, page-level namespaces, and `yaffo:app-init-complete` | ongoing | fewer chained init calls and one explicit app-ready boundary |
| 5. Phase-1 unit tests (`utils`, `index_photos`, `multi-select`, `i18n`) | ongoing | core logic covered |
| 6. CI workflow: `npm run test:unit` + `npm run typecheck:js` on check-in (`checks.yml`) | Done | regressions caught on PRs |
| 7. Phase-2 page modules as touched | ongoing | coverage ratchets up |
| 8. *(optional, later)* evaluate Option 2 full-TS build | 2–3 days | only if JSDoc verbosity bites |

---

## 6. Risks & open questions

- **Separate JS test targets.** Vitest now covers first-party app JavaScript,
  while `yaffo_ui_tests` continues to own browser/E2E tooling. Keep that boundary
  clear so app unit tests do not inherit E2E framework concerns.
- **The loading shim is glue.** It depends on modules being side-effect-evaluable
  against a global. Converting modules to real ESM `export`s later would remove the
  shim but is a larger change touching every `<script>` tag in templates.
- **Packaging (Option 2 only).** A TS build must be inserted *before* PyInstaller
  bundles `yaffo/static/`; given prior packaging fragility, this needs care. Option
  1 sidesteps it entirely.
- **Coverage targets.** Start with none; set per-file floors after Phase 1 to avoid
  a vanity whole-tree percentage that punishes untested vendored/page glue.
