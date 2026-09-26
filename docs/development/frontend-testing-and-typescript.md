# Frontend JavaScript: Unit Testing & Type Checking

Scope: first-party browser JavaScript under `yaffo/static/**` (vendored libraries
in `yaffo/static/vendor/` are excluded from linting, type checking, and coverage).

This page describes the current conventions for unit-testing that code with
**Vitest + jsdom** and type-checking it with **JSDoc + `checkJs`**. The app still
ships JavaScript with **no build step**: the same `.js` files that are tested and
type-checked are served verbatim by Flask and bundled verbatim by PyInstaller.

---

## 1. Tooling at a glance

| Concern | Tool | Config |
|---------|------|--------|
| Unit tests | Vitest 2 with the `jsdom` environment | `vitest.config.js` |
| Coverage | `@vitest/coverage-v8` | `vitest.config.js` → `coverage` |
| Type checking | TypeScript `tsc` over JSDoc-annotated `.js` (`allowJs`, `checkJs`, `noEmit`, `strict`) | `tsconfig.json` |
| Ambient types | `window.PHOTO_ORGANIZER`, `AppConfig`, `I18nService`, etc. | `yaffo/static/types/global.d.ts` |
| Lint | ESLint (`@eslint/js` recommended, browser globals, classic scripts) | `eslint.config.js` |
| Browser/E2E | Playwright (separate package) | `yaffo_ui_tests/` |

All frontend unit-test tooling lives at the **repo root** (`package.json`), separate
from `yaffo_ui_tests/`, which owns browser/E2E tests and has its own Jest suite for
its `lib/` framework. Keep that boundary: app unit tests go in `tests_js/`, never in
`yaffo_ui_tests/`.

Decisions that shape the setup:

- **Types are JSDoc + `checkJs`, type-check only.** No `.ts` sources, no bundler,
  no transpile. JSDoc comments ship as-is (stripping them would be a build step).
- **Full TypeScript is deferred, not rejected.** `allowJs` keeps a gradual
  `.js` → `.ts` path open if JSDoc verbosity ever becomes the bottleneck; adopting
  it would require a build step wired into dev, CI, and PyInstaller packaging.

---

## 2. Running the checks

| Command | Purpose |
|---------|---------|
| `npm run test:unit` | One-shot Vitest run; exits non-zero on failure |
| `npm run test:unit:watch` | Watch mode for the dev loop |
| `npm run test:unit:cov` | One-shot run with V8 coverage |
| `npx vitest run tests_js/utils.test.js` | A single file |
| `npx vitest run -t "handleRecord"` | Tests whose name matches |
| `npm run typecheck:js` | `tsc --project tsconfig.json` (no emit) |
| `npm run lint:js` | ESLint over `yaffo/static` |

Bare `vitest` defaults to watch mode — use `vitest run` (or the npm scripts)
anywhere that must terminate.

Any change under `yaffo/static/` should pass `lint:js`, `typecheck:js`, and
`test:unit` before it is done; `pytest` does not exercise this code. If the change
affects a user flow, also update the relevant Playwright specs in `yaffo_ui_tests/`.

### Coverage

Coverage is scoped to `yaffo/static/**/*.js` minus `vendor/`, and written to
`tests_js/coverage/` (git-ignored) as a terminal table, browsable HTML, `lcov`,
and `json-summary`. There is **no coverage threshold** yet; if one is added, scope
it to tested files rather than the whole tree.

### CI

`.github/workflows/checks.yml` runs a frontend job on pull requests to `master`: `npm ci` → `lint:js` → `typecheck:js` →
`test:unit:cov` (with a JUnit reporter). The job publishes the JUnit report as a
check run, uploads `tests_js/coverage/` as an artifact, and adds a coverage table
to the job summary via `scripts/coverage_summary.py`.

---

## 3. Unit tests

### 3.1 Layout

- Tests live in `tests_js/`, mirroring `yaffo/static/`:
  `yaffo/static/components/cron_builder.js` →
  `tests_js/components/cron_builder.test.js`.
- File names use `snake_case` and end in `.test.js` (Vitest only collects
  `tests_js/**/*.test.js`). Hyphenated source files still get snake-case tests,
  e.g. `multi-select.js` → `multi_select.test.js`.
- Shared harness code lives in `tests_js/support/`; data fixtures in
  `tests_js/fixtures/`.

`describe`, `it`, `expect`, `vi`, `beforeEach`, and `afterEach` are **globals**
(`globals: true`) — test files do not import them from `vitest`.

### 3.2 Loading a static module: `loadModule`

Static modules are classic scripts that attach to `window.PHOTO_ORGANIZER` as a
side effect; they have no ESM exports. Load them through
`tests_js/support/load_module.js`:

```js
import { loadModule } from '../support/load_module.js';

const PO = await loadModule('components/cron_builder.js');
const { createCronBuilder } = PO.COMPONENTS;
```

`loadModule(relPath)` takes a path relative to `yaffo/static/`, imports the real
file through Vite's pipeline (so V8 coverage maps to the source), and appends a
cache-busting query so **every call re-evaluates the file** against the current
jsdom globals. Call it inside each test (or a per-file helper) after arranging
globals and DOM — never once at the top of the file.

When a module depends on another module, load the dependency first, in the same
order the page's `<script>` tags use.

### 3.3 Shared globals: `tests_js/support/setup.js`

The setup file runs **before every test** and resets the implicit globals that
`base.html` wires up in production:

| Global | Test value |
|--------|------------|
| `window.APP_CONFIG` | `{ i18n: { locale: 'en-US', ... }, urls: {}, buildUrl }` — `buildUrl('x', { id: 1 })` → `/x/id/1` |
| `window.PHOTO_ORGANIZER` | Fresh `{ COMPONENTS: {}, i18n, i18nReady }` |
| `window.testI18n` | Fake `I18nService`: `t` returns the key; `number`/`percent`/`date`/`relativeTime`/`list` use real `Intl` in `en-US` |
| `window.i18next` | `{ init, t }` mocks; `t` returns `key:{json options}` |
| `window.notification` | `success`/`error`/`failure`/`warning`/`info`/`show` as `vi.fn()` |
| `window.matchMedia` | Non-matching stub (jsdom has none) |
| `window.testHelpers` | Factories listed below |

`window.testHelpers`:

- `createTestI18n(overrides)` — a fresh fake i18n service with selected methods
  overridden.
- `installTestI18next()` — reinstall the `window.i18next` mock and return it.
- `response(body, { ok, status })` — a minimal `fetch` `Response` whose `json()`
  resolves to `body`.
- `stubCatalogFetch(catalogs)` — stub `fetch` to serve a URL → JSON map (404 for
  unknown URLs; an `Error` value rejects).

Because `t` returns the key, assert on **translation keys**, not English copy:
`expect(el.textContent).toBe('components:cron.weeklyOnAt')`.

Pass `window.testI18n` (or a `createTestI18n(...)` variant) into factories as the
injected `i18n`, exactly as the page passes `app.i18n`. Add new app-wide globals
to `setup.js` rather than re-stubbing them in each test file.

### 3.4 DOM fixtures

Tests set `document.body.innerHTML` to the markup they need, usually in the test
itself or a `beforeEach`:

- **Modules that build their own DOM** only need the hollow containers the server
  renders (an empty `#scan-results`, a mount `div[data-cron-builder]`). Keep these
  fixtures tiny and inline.
- **Modules that enhance server-rendered markup** need the attributes and
  structure the Jinja template produces. Copy only the parts the module reads
  (ids, `name`s, `data-*` attributes), and keep them in sync when the template
  changes. A shared fixture string at the top of the file (e.g. `FORM_HTML` in
  `tests_js/filters/client_filter.test.js`) is fine when several tests use it.
- **Server-owned data the browser mirrors** belongs in `tests_js/fixtures/` as
  JSON generated from the Python source, with a pytest drift guard that fails
  when they diverge. Example: `tests_js/fixtures/media_filter_config.json` is
  imported by `client_filter.test.js` and checked against
  `client_filter_config()` by
  `tests/yaffo/domain/test_media_filter_params.py::test_js_fixture_matches_the_table`,
  whose docstring holds the regeneration command.

Do not reimplement Jinja in JavaScript or maintain parallel JS templates.

### 3.5 Mocks, timers, and cleanup

- **Network:** stub `fetch` with `vi.stubGlobal('fetch', vi.fn(...))`, returning
  `window.testHelpers.response(...)`. Stub other browser APIs jsdom
  lacks the same way.
- **Spies:** `vi.spyOn(obj, 'method')` for existing functions.
- **Time:** `vi.useFakeTimers()` for debounce/polling/relative-time logic;
  pair it with `vi.useRealTimers()` in `afterEach`.
- **Cleanup:** anything a test stubs outside the globals `setup.js` resets must
  be undone in `afterEach` — `vi.unstubAllGlobals()`, `vi.restoreAllMocks()`,
  `vi.useRealTimers()`. The config does not auto-restore mocks.
- **Events:** drive behavior through real DOM events
  (`el.dispatchEvent(new CustomEvent('htmx:afterSwap', { bubbles: true }))`,
  `el.click()`, `input` events) rather than calling private handlers.

### 3.6 What to test

Test through the module's **public API** — the object returned by `create*`/
`init*` factories or installed on the page namespace — and the DOM it produces.
If a test needs an internal helper (e.g. an NDJSON record handler), return it
from the factory's API instead of reaching into the closure.

Prioritize logic with real branching: parsing, formatting, filtering, state
machines, and the dependency boundary itself:

- `create*` factories are synchronous and do not wait on `i18nReady`.
- `init*` initializers store the shared instance on
  `window.PHOTO_ORGANIZER.COMPONENTS` and wire HTMX re-initialization.
- Page functions use the dependencies passed in rather than globals.

Pure layout and end-to-end flows belong in the Playwright suite. Add unit tests
for page modules when you touch them; there is no requirement to backfill.

### 3.7 Example

```js
// tests_js/components/cron_builder.test.js
import { loadModule } from '../support/load_module.js';

const loadCronBuilderComponent = async () => {
  await loadModule('components/cron_builder.js');
  return window.PHOTO_ORGANIZER.COMPONENTS;
};

describe('cronBuilder', () => {
  it('initializes, stores, and wires the shared cron builder', async () => {
    document.body.innerHTML = '<div data-cron-builder data-cron-name="schedule"></div>';
    const components = await loadCronBuilderComponent();

    const cronBuilder = components.initCronBuilder({ i18n: window.testI18n, document });

    expect(components.cronBuilder).toBe(cronBuilder);
    expect(document.querySelector('[data-cron-builder]').dataset.cronReady).toBe('1');

    document.body.insertAdjacentHTML('beforeend', '<div id="swap"><span data-cron="0 9 * * 1"></span></div>');
    document.getElementById('swap').dispatchEvent(new CustomEvent('htmx:afterSwap', { bubbles: true }));

    expect(document.querySelector('#swap [data-cron]').textContent).toBe('components:cron.weeklyOnAt');
  });
});
```

---

## 4. Type checking (JSDoc + `checkJs`)

### 4.1 Configuration

`tsconfig.json` sets `allowJs`, `checkJs`, `noEmit`, `strict`, `target: ES2022`,
DOM + ES2022 (incl. `Intl`) libs, and `moduleDetection: "force"` so each classic
script is its own scope for the checker. It type-checks an explicit **`files`
list** — a static file is only checked once it is added there. The first entry is
`yaffo/static/types/global.d.ts`, which declares the shared ambient types
(`AppConfig`, `I18nService`, `window.PHOTO_ORGANIZER`, component APIs).

### 4.2 Conventions

- Start each checked file with `// @ts-check`.
- Declare named `@typedef`s for injected dependencies, the public API return
  shape, important DOM roots, and non-trivial data records. Put types shared
  across files in `types/global.d.ts`.
- Annotate public factories with `@param` / `@returns`.
- Keep casts at DOM boundaries (`querySelector`, `event.target`, `dataset`,
  `value`) — e.g. `/** @type {HTMLInputElement} */ (root.querySelector(...))` —
  not scattered through business logic.
- Handle nullable DOM lookups deliberately: guard elements that may be missing;
  cast only when the component just rendered the markup itself.
- Use a **file-specific typed window alias** (e.g. `cronBuilderWindow`) rather
  than a generic top-level name like `appWindow`, which collides across classic
  scripts sharing the global scope.

---

## 5. Module structure that keeps code testable

These conventions come from the initialization model in `static/app.js` and are
what the harness above assumes. `components/cron_builder.js` and
`utilities/automations.js` are the reference implementations.

### 5.1 Components: `create*` and `init*`

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

- **`create*`** receives dependencies and returns an API object. It is
  synchronous: no `i18nReady.then(...)`, no global listeners, no namespace
  mutation beyond installing the factory.
- **`init*`** calls `create*`, stores the shared instance on
  `window.PHOTO_ORGANIZER.COMPONENTS`, performs initial DOM setup, and wires HTMX
  re-initialization.
- Prefer injected dependencies over promise globals such as `thingReady`.

### 5.2 App initialization and page entrypoints

`static/app.js` owns the async boundary. It initializes i18n and app-level
components under `window.PHOTO_ORGANIZER.COMPONENTS`, then dispatches
`yaffo:app-init-complete` with the ready app object. Pages consume
`app.COMPONENTS`; they do not re-bootstrap app-wide components or call
`window.PHOTO_ORGANIZER.i18nReady.then(...)` themselves.

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

Page modules install their API under a **page-level namespace**, not as flat
`window.PHOTO_ORGANIZER.init*` functions:

```js
window.PHOTO_ORGANIZER = window.PHOTO_ORGANIZER || {};
window.PHOTO_ORGANIZER.automations = window.PHOTO_ORGANIZER.automations || {};

const automations = window.PHOTO_ORGANIZER.automations;

automations.initTriggerEditor = (i18n, cronBuilder) => {
    // page behavior
};
```

---

## 6. Checklist: adding tests and types to a static file

- [ ] Identify the public entrypoint: `createThing(deps)`, `initThing(deps)`, or a
  page-namespace function under `window.PHOTO_ORGANIZER.<pageName>`.
- [ ] Keep runtime factories synchronous; no hidden `i18nReady.then(...)`.
- [ ] Page orchestration listens for `yaffo:app-init-complete` and passes
  `app.i18n`, `window.APP_CONFIG`, and components in explicitly.
- [ ] App-level/shared components are initialized in `static/app.js`.
- [ ] Add `// @ts-check`, JSDoc typedefs for deps/API/DOM roots, and casts only
  at DOM boundaries; use a file-specific window alias.
- [ ] Add the file to `tsconfig.json`'s `files` list.
- [ ] Add or update `tests_js/<mirrored path>.test.js` using `loadModule` and
  the `setup.js` globals; clean up any extra stubs in `afterEach`.
- [ ] Test the dependency boundary: synchronous factory, stored shared instance,
  injected dependencies used.
- [ ] Run `npm run lint:js`, `npm run typecheck:js`, and `npm run test:unit`.
