# Internationalization proposal

## Goals

Yaffo should support:

- translated server-rendered templates, flash messages, validation errors, and
  JSON error messages;
- translated text created dynamically by JavaScript;
- locale-aware dates, times, numbers, percentages, and plural forms;
- one selected locale shared by Flask and JavaScript;
- offline operation without a translation CDN;
- incremental migration without requiring every page to be translated at once.

English remains the source and fallback language. The first additional locale
should be chosen to exercise pluralization and longer text; German or French are
good initial validation locales. Add an RTL locale later as a separate layout
validation milestone.

## Recommended libraries

### Server: Flask-Babel

Add `Flask-Babel` as an application dependency. It integrates Babel and gettext
with Flask and Jinja, providing:

- `gettext`, `pgettext`, `ngettext`, and `npgettext`;
- extraction from Python and Jinja into gettext catalogs;
- locale-aware date, time, number, decimal, percent, and currency formatting;
- request-scoped locale and timezone selectors.

Translation files should use the standard gettext layout:

```text
babel.cfg
messages.pot
yaffo/translations/
  de/
    LC_MESSAGES/
      messages.po
      messages.mo
```

Use Babel's `pybabel` CLI for extraction, initialization, updates, and
compilation. Add Invoke tasks so contributors do not need to remember raw
commands.

### Browser: i18next

Vendor the browser build of `i18next` under `yaffo/static/vendor/`; Yaffo must
not depend on a CDN at runtime. Use one JSON resource per locale, with feature
namespaces represented as top-level objects:

```text
yaffo/static/locales/
  en.json
  de.json
```

i18next is preferable to a hand-written lookup function because it handles
plural categories, interpolation, fallback languages, namespaces, missing-key
diagnostics, and language changes consistently. The catalogs are small,
packaged local assets, so preload the selected locale's complete browser
catalog instead of adding lazy namespace loading.

Use native `Intl` APIs for values:

- `Intl.NumberFormat`
- `Intl.DateTimeFormat`
- `Intl.RelativeTimeFormat`
- `Intl.ListFormat`

i18next can delegate number/date/list formatting to `Intl`; Yaffo should also
expose small wrapper functions for code that only needs formatting and no
translated sentence.

### Why use two catalog formats?

Server gettext catalogs are the established Flask/Jinja workflow and support
context and plural extraction well. JSON is i18next's native authoring format
and is straightforward to validate from JavaScript and pytest. Keeping each
runtime in its conventional format avoids maintaining custom PO-to-i18next
conversion code. This is a tooling choice, not a deployment or network-loading
requirement: both catalog sets ship inside the local application.

The shared contract is the locale and terminology, not the file format. Common
terms such as Cancel, Save, Delete, Photo, Video, Face, and Person should be
reviewed together across both catalogs. A future CI check may compare a declared
set of shared terms if drift becomes a problem.

## Locale selection and persistence

Yaffo is primarily a single-user desktop application. Store the selected locale
as an `ApplicationSettings` value named `locale`; do not add it to media-domain
tables.

Resolution order:

1. explicit locale saved in `ApplicationSettings`;
2. best supported match from `Accept-Language`;
3. `en`.

The locale selector must only return values from a configured allowlist such as
`SUPPORTED_LOCALES = ("en", "de")`. Never use an arbitrary request value as a
catalog path.

Expose these values in the global application configuration:

```javascript
window.APP_CONFIG.i18n = {
    locale: "de",
    fallbackLocale: "en",
    resourceUrl: "/static/locales/{{lng}}.json"
};
```

Flask determines `current_locale` for every request and renders it into
`APP_CONFIG.i18n.locale`. The base page initializes i18next once, before feature
modules run:

```javascript
const locale = window.APP_CONFIG.i18n.locale;
const resourceUrl = window.APP_CONFIG.i18n.resourceUrl.replace('{{lng}}', locale);
const resources = await fetch(resourceUrl).then(response => response.json());

await i18next.init({
    lng: locale,
    fallbackLng: window.APP_CONFIG.i18n.fallbackLocale,
    resources: {
        [locale]: resources
    },
    ns: Object.keys(resources),
    defaultNS: 'common'
});
```

The URL template resolves to `/static/locales/de.json`. That single resource
contains `common`, `faces`, `media`, `utilities`, and other feature namespaces.
The bootstrap loads it before calling any page initialization function. Pages
therefore do not pass namespace lists through Jinja, and HTMX swaps cannot
introduce an unloaded catalog dependency.

Set the document metadata from the selected locale:

```html
<html lang="{{ current_locale }}" dir="{{ text_direction }}">
```

The Settings page should provide a language selector. Saving it may reload the
page; live language switching is not required for the first implementation.

## Server conventions

### Python

Translate user-facing content at the presentation boundary:

```python
from flask_babel import gettext, ngettext

flash(gettext("Person not found"), "error")

message = ngettext(
    "%(count)s face selected",
    "%(count)s faces selected",
    count,
)
```

Conventions:

- Import translation functions at module scope.
- Translate flash messages and browser-facing JSON error/message values.
- Do not translate logs, exception details, database values, event names, job
  names, API field names, automation handler names, or URL paths.
- Do not assemble sentences from translated fragments. Translate the complete
  sentence with named placeholders.
- Use `pgettext` when an English word has multiple meanings, for example
  `pgettext("button", "Open")`.
- Use `ngettext` for every count-dependent sentence. Do not append `"s"` in
  Python or Jinja.
- Keep interpolation values out of the source text and use named placeholders.
- Stable machine-readable API clients should receive error codes in addition to
  localized human text:

  ```json
  {"error": "Directory path is required", "code": "directory_required"}
  ```

  Internal HTMX/browser-only endpoints may migrate to codes later, but newly
  introduced wire contracts should include them.

### Jinja templates

Use Jinja's gettext integration:

```jinja2
<h2>{{ _("Photo details") }}</h2>
<input placeholder="{{ _('Search file or folder name…') }}">
<button aria-label="{{ _('Close') }}">&times;</button>
```

For interpolation and pluralization, prefer trans blocks:

```jinja2
{% trans count=faces|length %}
  Showing {{ count }} face
{% pluralize %}
  Showing {{ count }} faces
{% endtrans %}
```

Template conventions:

- Translate visible text, titles, placeholders, `alt`, `title`, and
  `aria-label` values.
- Do not translate CSS classes, element IDs, data keys, route names, file paths,
  media metadata, user-entered names, tags, locations, labels, or custom page
  content.
- Keep HTML outside translation strings when practical. When emphasis or links
  must occur inside a sentence, use a trans block with simple placeholders.
- Macros should accept already translated labels when they are generic
  presentation components. Feature-specific macros may translate their own fixed
  copy.
- Replace manual singular/plural expressions such as
  `face{{ 's' if count != 1 else '' }}` with gettext plural forms.
- Set Jinja whitespace carefully around `{% trans %}` blocks and test the
  rendered output.

## JavaScript conventions

Initialize one namespaced service before feature modules:

```javascript
window.PHOTO_ORGANIZER.i18n = {
    t: (key, options) => i18next.t(key, options),
    number: (value, options) =>
        new Intl.NumberFormat(i18next.language, options).format(value),
    date: (value, options) =>
        new Intl.DateTimeFormat(i18next.language, options).format(new Date(value)),
    relativeTime: (value, unit, options) =>
        new Intl.RelativeTimeFormat(i18next.language, options).format(value, unit),
};
```

Feature modules should receive this service in their initializer, before
`window.APP_CONFIG`:

```javascript
window.PHOTO_ORGANIZER.initIndexPhotos(data, window.PHOTO_ORGANIZER.i18n, window.APP_CONFIG);
```

Use semantic, namespaced keys:

```javascript
i18n.t('utilities:indexPhotos.syncStarted')
i18n.t('common:resultsShown', { count })
```

Example resource:

```json
{
  "resultsShown_one": "Showing {{count}} result",
  "resultsShown_other": "Showing {{count}} results"
}
```

Conventions:

- No new hard-coded user-facing strings in JavaScript.
- Use i18next interpolation; do not concatenate translated fragments.
- Pass `count` for pluralized messages.
- Use `textContent` for translated plain text. If HTML is required, build DOM
  nodes explicitly rather than translating arbitrary `innerHTML`.
- Do not translate values received from the server unless they are documented
  enum codes. The server should localize human-readable errors.
- Use the selected application locale explicitly with `Intl`; do not use
  `undefined` and silently follow a browser locale different from the server.
- Keep raw values in `data-*` attributes and format only for display.
- `N/A`, status labels, button states such as `Applying…`, confirm-dialog
  defaults, filesystem picker copy, cron descriptions, and notification
  messages are all translatable UI.
- Generated custom widgets should receive locale and formatting helpers through
  their host data. Widget templates must not hard-code `en-US`.
- LLM-backed features should receive the selected application locale and this
  response-language rule in their system prompt: respond in the language used
  in the user's latest message; when that language is ambiguous, use the
  application locale. Fixed application UI copy still belongs in translation
  catalogs and must not be delegated to the model.

## Dates, times, numbers, and units found in the current code

### Dates and times

The current server filter in `yaffo/template_filters.py` uses fixed English
`strftime` patterns:

- `%b %d, %Y`
- `%b %d, %Y, %I:%M %p`
- `%I:%M %p`

These appear in the gallery, media details, faces, job status, and automation
run history. Replace display formatting with Flask-Babel's `format_date`,
`format_datetime`, and `format_time` using named widths such as `medium` rather
than locale-specific patterns.

Preserve the existing timestamp distinction:

- app-generated job/run timestamps are UTC and must be converted to the selected
  user timezone before display;
- `MediaItem.date_taken` is a camera-local wall-clock value with no reliable
  timezone and must be formatted without timezone rebasing.

The current browser helper in `static/utils.js` already uses `Intl`, including
relative time, but passes `undefined` as the locale. Change it to the selected
application locale. Generated widget templates in
`site_agents/widget_templates.py` explicitly use `en-US`; remove that locale and
route formatting through the host helper.

Video duration is elapsed time, not a wall-clock date. Keep the current
`H:MM:SS` formatter for compact media duration, but format any leading hour
count with locale-aware digits if non-Latin numbering systems are supported.

### Counts and pluralization

Counts currently appear unformatted or use English-only suffix logic in:

- gallery result summaries;
- faces and people counts;
- pagination summaries;
- duplicate group/file totals;
- job progress and scan statistics;
- labels, tags, faces, and people section headings;
- JavaScript filesystem scan summaries.

Use `format_number`/`Intl.NumberFormat` for display and `ngettext`/i18next
plural rules for surrounding sentences. Pluralization must not assume only
singular and plural categories.

### Percentages, decimals, and confidence values

Current examples include:

- job progress rendered with `"%.2f" + "%"`;
- face similarity rendered with `toFixed(2)` or integer plus `%`;
- label confidence rendered with `%.2f`;
- automation progress values.

Use Flask-Babel `format_percent` or `Intl.NumberFormat` with
`style: "percent"`. Confirm whether stored values are fractions (`0.95`) or
already percentages (`95`) before formatting; normalize each contract rather
than guessing in the view.

### Coordinates

Latitude/longitude are currently formatted with fixed decimal points and
English compass letters. Use locale-aware decimal formatting for the numeric
portion. Translate or replace compass letters with localized direction labels,
while retaining raw decimal coordinates in machine-readable payloads.

### Distances and file sizes

The proximity filter currently exposes miles in visible copy. Treat the stored
distance unit as a separate product decision:

- minimal implementation: localize the word “miles” but keep behavior fixed;
- preferred implementation: add a measurement preference and convert between
  miles and kilometers at the UI boundary.

Any new file-size display should use a dedicated byte formatter with localized
numbers and stable binary/decimal unit policy. Do not pass byte counts through a
generic decimal formatter without unit handling.

## Catalog and extraction workflow

Add `babel.cfg`:

```ini
[python: yaffo/**.py]
keywords = _ gettext ngettext pgettext npgettext lazy_gettext

[jinja2: yaffo/templates/**.html]
extensions = jinja2.ext.i18n
```

Add Invoke tasks:

- `inv i18n-extract` — update `messages.pot`;
- `inv i18n-init --locale=de` — create a server PO and browser locale catalog;
- `inv i18n-update` — merge new server strings and report missing browser keys;
- `inv i18n-translate --locale=de` — detect untranslated server messages and
  browser keys, translate only those entries from the English source catalogs,
  and write the proposed translations into the target locale;
- `inv i18n-compile` — compile `.po` to `.mo` and validate every JSON resource;
- `inv i18n-check` — fail on malformed catalogs, missing English browser keys,
  fuzzy production entries, untranslated keys in required locales, or new
  hard-coded user-facing text in Jinja and JavaScript.

The hard-coded text check uses a checked-in fingerprint baseline because the
application is being migrated incrementally. Existing untranslated strings are
allowed, but adding a new literal or another occurrence of an existing literal
fails the test. Update the baseline only when intentionally accepting new
translation debt:

```shell
python -m yaffo.scripts.i18n_hardcoded --write-baseline
```

The scanner covers visible Jinja text and static `alt`, `aria-label`,
`placeholder`, and `title` attributes. In JavaScript it covers literals written
to DOM text/HTML properties, notifications, confirmation-dialog options,
placeholders, and user-facing fallback strings. It intentionally does not scan
every JavaScript string because selectors, routes, CSS classes, event names,
and protocol values are not translatable UI text.

`i18n-translate` should use the application's configured LLM provider rather
than a separate translation service. Its translation prompt must include:

- the target locale;
- the English source text;
- message context or namespace/key;
- developer comments, when present;
- plural forms and all interpolation placeholders.

The task must preserve gettext placeholders such as `%(count)s`, i18next
placeholders such as `{{count}}`, HTML tags, and ICU/gettext plural structure
exactly. It must never overwrite a non-empty human translation unless
`--overwrite` is explicitly supplied. Support `--dry-run` to print proposed
changes without writing and `--keys-only` to report missing entries without
calling the model.

Translate entries in bounded batches, request structured JSON output, and
validate every returned key and placeholder set before modifying a catalog. If
any entry fails validation, leave that entry unchanged and fail the task with a
specific diagnostic. Automatically generated translations should be marked
fuzzy in PO catalogs and recorded in JSON metadata or a sidecar review file
until reviewed by a person.

Do not edit `.mo` files manually. Commit `.po`, `.mo`, English JSON, and
translated JSON so packaged/offline builds contain all resources.

## Testing conventions

Add tests for:

- locale selection priority and unsupported-locale fallback;
- `<html lang>` and `dir`;
- server gettext in templates, flash messages, and JSON errors;
- singular and plural rendering for at least `0`, `1`, `2`, and a locale with
  more than two plural categories before claiming broad locale support;
- locale-aware date, decimal, grouping, and percent output;
- UTC job timestamps versus non-rebased camera-local capture dates;
- JavaScript translation lookup, interpolation, pluralization, and fallback;
- JavaScript formatting using the application locale rather than browser
  defaults;
- LLM prompts include the selected locale and explicit response-language rule,
  with tests covering a non-English user message and an ambiguous/empty message
  that falls back to the application locale;
- extraction/compile checks in CI;
- no untranslated key tokens appearing in rendered pages.

Tests should set locale explicitly. They must not depend on the developer
machine's locale or timezone.

### Catalog parity and placeholder tests

Add unit tests that recursively compare every translated browser JSON catalog
against the English source catalog, namespace by namespace:

- translated catalogs contain exactly the same leaf keys as English;
- no translated catalog has missing or unexpected keys;
- every translated value has the same interpolation placeholders as English;
- plural variants have the required base and locale-specific plural forms;
- values expected to be strings are strings in every locale.

Placeholder comparison must parse tokens rather than compare raw text. For
example, these have matching placeholders despite different word order:

```json
{"results": "Showing {{start}}–{{end}} of {{total}} results"}
{"results": "{{total}} Ergebnisse, {{start}}–{{end}} werden angezeigt"}
```

Do the equivalent for gettext catalogs:

- every compiled locale has the same message IDs and message contexts as the
  POT/English source;
- translations preserve Python/gettext named placeholders such as
  `%(count)s`;
- singular and plural entries preserve the same placeholder set;
- fuzzy or empty translations fail for locales designated release-ready.

Implement these checks as ordinary pytest tests so they run locally and in CI.
The tests should report the locale, namespace/message ID, missing keys, extra
keys, and placeholder differences in the assertion message.

## Work breakdown

### Implementation status

Status as of June 25, 2026:

- [x] Phase 1: Flask-Babel/i18next infrastructure, locale persistence,
  extraction/compile tasks, translation tooling, and catalog validation.
- [x] Phase 2: locale-aware server/browser formatting foundation, including the
  UTC timestamp versus camera-local capture-date distinction.
- [x] Phase 3: shared shell, filters, reusable components, and their JavaScript
  modules.
- [x] Phase 4.1: home gallery and media details vertical slice.
- [x] Phase 4.2: faces and people workflows.
- [x] Phase 4.3: locations map/list.
- [x] Phase 4.4: settings.
- [ ] Phase 4.5–4.9: remaining feature screens and LLM prompt behavior.
- [ ] Phase 5: translation and release-readiness work.

The completed home/media slice includes:

- `templates/index.html`, `templates/media/view.html`, and the tag filter;
- `routes/home.py` and `routes/media.py` browser-facing messages;
- favorites, inline video, location autocomplete, filter configuration, media
  actions, tag editing, and face-reassignment JavaScript;
- gettext and i18next English/German catalog entries;
- localized JSON error text with stable error codes;
- German rendering tests for the gallery, media details, and API errors.

The completed faces inbox slice includes:

- the assignment workflow template, help content, filters, pagination, and empty
  states;
- browser notifications for skipping, validation, and person creation;
- localized assignment API responses with plural forms and stable error codes;
- corrected Babel extraction signatures for Python plural/context functions;
- German rendering and API pluralization tests.

The completed people slice includes:

- the people list, add/edit/delete forms, localized birthdates, and route
  validation/flash messages;
- the person face gallery, similarity filters, selection controls, tooltips, and
  removal workflow;
- delegated browser actions, standard select controls, and global confirmation
  dialogs instead of inline handlers or native alerts;
- German rendering, API, flash, pluralization, and removal-validation tests.

The completed settings media-directory slice includes:

- media and thumbnail-directory controls, current-path statistics, and system
  path information;
- delegated browser actions, locale-aware counts and byte sizes, and escaped
  directory paths in dynamic markup;
- localized API and stream failures with stable error codes;
- German rendering and API validation tests.

The completed settings label-management slice includes:

- the label vocabulary description, filter, empty states, form fields, default
  badges, and reclassification controls;
- localized HTMX validation and task notifications, including pluralized
  reclassification counts;
- German fragment, validation, and pluralization tests.

The completed settings LLM slice includes:

- the AI generation description, model selector, provider key status, key forms,
  and environment/keychain state labels;
- localized Anthropic model descriptors and model-update notifications;
- German rendering and response tests, including a guard against mutating shared
  model metadata between locale requests.

Current verification covers catalog key/placeholder parity, compiled gettext
catalogs, JavaScript and Python syntax, non-English rendering, and the
hard-coded user-facing text baseline.

### Phase 1: infrastructure — complete

- [x] Add and configure Flask-Babel.
- [x] Add `locale` to application settings and a Settings-page selector.
- [x] Implement locale resolution, `current_locale`, and text direction.
- [x] Add gettext extraction configuration and Invoke tasks.
- [x] Vendor i18next and initialize it from the selected locale's packaged JSON
   resource.
- [x] Render the selected locale and resource URL into the base template, then
   block page-module initialization until i18next loads the catalog.
- [x] Add English server and browser catalogs.
- [x] Add locale-aware wrappers under `window.PHOTO_ORGANIZER.i18n`.
- [x] Add `inv i18n-translate --locale=<locale>` to detect and automatically
   translate missing PO messages and browser JSON keys from their English
   source text, with dry-run, placeholder validation, and review markers.
- [x] Add unit tests for locale selection, both translation runtimes, catalog key
   parity, and placeholder parity.

### Phase 2: formatting foundation — complete

- [x] Replace the fixed `strftime` template filter with Babel-backed date helpers.
- [x] Preserve separate UTC-timestamp and camera-local-date code paths.
- [x] Add server filters/helpers for integers, decimals, percentages, and
   coordinates.
- [x] Update `static/utils.js` to use the application locale.
- [x] Remove `en-US` from generated widget templates and pass host locale helpers.
- [x] Migrate pagination, progress, similarity, confidence, scan counts, and
   duplicate totals.
- [x] Add locale/timezone regression tests.

### Phase 3: shared shell and components — complete

Migrate the highest-reuse UI first:

- [x] `base.html` navigation, page strip, flash close controls, folder picker, and
   confirm dialog.
- [x] Modal, info-modal, pagination, page-header, file-browser, notification,
   searchable-select, multi-select, chat-dialog, and cron-builder components.
- [x] Shared filters and empty/error states.
- [x] Corresponding JavaScript modules and default messages.

Completing this phase removes a large percentage of repeated English copy and
establishes examples for feature work.

### Phase 4: feature-by-feature migration — in progress

Migrate one vertical slice at a time, including templates, routes, JavaScript,
and tests:

- [x] Home gallery and media details.
- [x] Faces and people.
- [x] Locations.
- [x] Settings.
- [ ] Indexing and duplicate utilities.
- [ ] Automations.
- [ ] Themes.
- [ ] Custom pages and page-builder UI.
- [ ] LLM-backed page, automation, and theme generation prompts: pass the selected
   locale and require replies in the latest user-message language, falling back
   to the application locale when ambiguous.

For each slice:

- extract server strings;
- add English JavaScript keys;
- replace manual pluralization and formatting;
- add one non-English rendering test;
- verify keyboard labels, placeholders, tooltips, and error paths.

#### Remaining screen checklist

Each item includes its templates and primary browser module. The corresponding
route modules and browser-facing errors are part of the same checklist item.

- [x] Faces inbox and assignment workflow:
  `templates/faces/index.html`, `static/faces/index.js`, and `routes/faces.py`.
- [x] People list:
  `templates/people/list.html`, `static/people/list.js`, and `routes/people.py`.
- [x] Person face gallery and reassignment/removal workflow:
  `templates/people/faces.html` and `static/people/faces.js`.
- [x] Locations map/list:
  `templates/locations/list.html`, `static/locations/list.js`, and
  `routes/locations.py`.
- [x] Settings media-directory controls:
  `templates/settings/index.html`, `static/settings/index.js`, and the related
  endpoints in `routes/settings.py`. The language selector itself is complete.
- [x] Settings label management:
  `templates/settings/_labels.html` and `static/settings/labels.js`.
- [x] Settings LLM provider and API-key forms:
  `templates/settings/_llm.html`,
  `templates/settings/_llm_api_key.html`, and their settings routes.
- [x] Utilities landing/navigation:
  `templates/utilities/_base.html` and `static/utilities/_base.js`.
- [x] Photo/video indexing:
  `templates/utilities/index_photos.html`,
  `static/utilities/index_photos.js`, and
  `routes/utilities/index_photos.py`.
- [x] Duplicate detection form, results, photo cards, counts, and actions:
  all `templates/utilities/remove_duplicates*.html` templates and
  `routes/utilities/remove_duplicates.py`.
- [x] Automation list/editor:
  `templates/utilities/automations.html`,
  `static/utilities/automations.js`, and
  `routes/utilities/automations.py`.
- [x] Automation run history:
  `templates/utilities/automations_runs.html`.
- [x] Automation trigger list and trigger editor:
  `templates/utilities/automations_triggers.html` and
  `templates/utilities/automations_triggers_edit.html`.
- [x] Theme gallery, generation, preview, and publishing:
  `templates/themes_page/index.html`, `static/themes_page/index.js`, and
  `routes/themes_page.py`.
- [ ] Custom page presentation and widget grid:
  `templates/pages/presentation.html`, `templates/pages/_grid.html`,
  `templates/pages/_widget.html`, `static/pages/detail.js`, and
  `static/pages/grid.js`.
- [ ] Page designer and chat-driven generation:
  `templates/pages/design.html`, page-builder JavaScript, and
  `routes/pages.py`.
- [ ] Widget frame/runtime messages and host formatting:
  `templates/pages/widget_frame.html`, `static/pages/widget_api.js`, and
  `static/pages/widget_broker.js`.
- [ ] LLM prompts for page, automation, and theme generation:
  `site_agents/prompt_generator/` and the generation task entry points.
- [ ] Final error-page review:
  `templates/404.html`, `templates/500.html`, and any remaining route/HTMX
  error fragments not covered by a feature slice.

### Phase 5: translation and release readiness — not started

- [ ] Complete and review the first non-English catalog.
- [ ] Run a pseudo-locale that expands text and adds visible delimiters.
- [ ] Audit layout overflow, tables, modals, and narrow controls.
- [ ] Add RTL metadata and layout testing before publishing an RTL locale.
- [ ] Verify PyInstaller includes `.mo`, JSON locale resources, and vendored
   i18next assets.
- [ ] Add contributor documentation for adding and updating locales.
- [ ] Add CI catalog validation and an untranslated-string review checklist.

## Non-goals for the initial migration

- Translating user-entered names, tags, locations, custom page content, AI
  responses, filenames, or filesystem paths.
- Translating logs or persisted machine identifiers.
- Runtime download of translations.
- Live language switching without a page reload.
- Automatic unit conversion before a product-level measurement preference is
  defined.

## References

- [Flask-Babel documentation](https://python-babel.github.io/flask-babel/)
- [Babel date and time formatting](https://babel.pocoo.org/en/latest/dates.html)
- [Babel number formatting](https://babel.pocoo.org/en/latest/numbers.html)
- [i18next pluralization](https://www.i18next.com/translation-function/plurals)
- [i18next formatting](https://www.i18next.com/translation-function/formatting)
- [JavaScript `Intl`](https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Global_Objects/Intl)
