# Internationalization standards

Yaffo is internationalized with Flask-Babel on the server and i18next in the
browser. English is the source and fallback language. Translation resources are
packaged with the app so the UI works offline and never depends on a translation
CDN at runtime.

This document is the reference for adding or changing user-facing text,
locale-aware formatting, translation catalogs, and i18n tests.

## Supported locales

The authoritative locale list lives in `yaffo/i18n.py`:

- `en` — English
- `de` — Deutsch
- `zh` — 中文
- `hi` — हिन्दी
- `es` — Español
- `ar` — العربية
- `fr` — Français

Locale values are normalized to the base language code. For example, `de-DE`
and `de_AT` resolve to `de`. Unsupported locales fall back to English.

Locale resolution order:

1. saved `ApplicationSettings` value named `locale`;
2. best supported `Accept-Language` match;
3. `en`.

Do not use arbitrary request values as catalog names or paths. All locale inputs
must pass through `normalize_locale()` or another allowlist-backed path.

`<html>` receives both locale and direction:

```jinja2
<html lang="{{ current_locale }}" dir="{{ text_direction }}">
```

`ar`, `fa`, `he`, and `ur` are treated as RTL by `text_direction()`.

## Runtime architecture

### Server

Server-rendered pages, flash messages, route validation messages, HTMX
fragments, and browser-facing JSON messages use Flask-Babel/gettext catalogs:

```text
babel.cfg
messages.pot
yaffo/translations/
  de/
    LC_MESSAGES/
      messages.po
      messages.mo
```

`yaffo/app.py` calls `init_i18n(app)` and injects:

- `current_locale`
- `supported_locales`
- `text_direction`

Use Flask-Babel formatting through the shared template filters in
`yaffo/template_filters.py` for dates, times, integers, decimals, percentages,
coordinates, and byte-size-like display values.

### Browser

The browser runtime uses vendored i18next:

```text
yaffo/static/vendor/i18next/25.7.4/i18next.min.js
yaffo/static/i18n.js
yaffo/static/locales/
  en.json
  de.json
  ...
```

`base.html` exposes the selected locale through `window.APP_CONFIG.i18n`:

```javascript
window.APP_CONFIG.i18n = {
    locale: "de",
    fallbackLocale: "en",
    resourceUrl: "/static/locales/__lng__.json"
};
```

`static/i18n.js` loads the selected locale catalog and English fallback catalog,
then exposes `window.PHOTO_ORGANIZER.i18nReady` and
`window.PHOTO_ORGANIZER.i18n`.

Feature modules that need translated text or locale-aware formatting must wait
for `i18nReady` and receive the service in their initializer:

```jinja2
{% block scripts %}
<script src="{{ url_for('static', filename='utilities/index_photos.js') }}"></script>
<script>
window.PHOTO_ORGANIZER.i18nReady.then((i18n) => {
    window.PHOTO_ORGANIZER.initIndexPhotos(
        {{ payload | tojson }},
        i18n,
        window.APP_CONFIG
    );
});
</script>
{% endblock %}
```

Pass `window.APP_CONFIG` as the final initializer argument, following the global
JavaScript module convention.

The i18n service provides:

- `t(key, options)`
- `number(value, options)`
- `percent(value, options)`
- `date(value, options)`
- `relativeTime(value, unit, options)`
- `list(values, options)`

Use those helpers instead of browser defaults. Do not call `Intl.*Format` with
an undefined locale, because that can diverge from the server-selected locale.

## What to translate

Translate:

- visible UI text;
- page titles, section headings, button labels, badges, empty states, and help
  text;
- placeholders, `alt`, `title`, and `aria-label` values;
- flash messages;
- browser-facing JSON error or notification messages;
- HTMX fragment validation messages;
- JavaScript notification, confirmation-dialog, loading, and fallback text;
- built-in system labels shown to the user, such as built-in themes,
  automations, units, and status labels.

Do not translate:

- user-entered names, tags, locations, captions, filenames, or filesystem paths;
- media metadata and custom page content;
- logs, exception details, internal event names, task names, handler names, URL
  paths, CSS classes, element IDs, API field names, database column names, or
  protocol values;
- AI-generated prose after it has been authored by the user/model.

Machine-readable API clients should receive stable codes in addition to
localized human text:

```json
{"error": "Directory path is required", "code": "directory_required"}
```

Do not add `success: false`; HTTP status carries failure state. For successful
acks, prefer `204` or redirect behavior.

## Python conventions

Import translation functions at module scope:

```python
from flask_babel import gettext, ngettext, pgettext
```

Translate at the presentation boundary:

```python
flash(gettext("Person not found"), "error")

message = ngettext(
    "%(count)s face selected",
    "%(count)s faces selected",
    count,
)
```

Rules:

- Translate complete sentences, not fragments assembled in code.
- Use named placeholders such as `%(count)s`.
- Keep interpolation values out of the source text.
- Use `ngettext` for every count-dependent message. Never append `"s"` in
  Python.
- Use `pgettext` when an English word has different meanings, such as
  `pgettext("button", "Open")`.
- Use `lazy_gettext` only for values declared at import time and resolved later
  during a request or app context, such as built-in theme labels.
- Do not translate data before persisting it. Persist stable source values or
  machine codes, then localize at render/response time.

## Jinja conventions

Use Jinja gettext integration:

```jinja2
<h2>{{ _("Photo details") }}</h2>
<input placeholder="{{ _('Search file or folder name') }}">
<button aria-label="{{ _('Close') }}">&times;</button>
```

Use `{% trans %}` blocks for interpolation and pluralization:

```jinja2
{% trans count=faces|length %}
  Showing {{ count }} face
{% pluralize %}
  Showing {{ count }} faces
{% endtrans %}
```

Rules:

- Translate static text in templates and reusable components.
- Do not translate CSS classes, element IDs, route names, data keys, paths, or
  user-provided values.
- Keep HTML outside translation strings when practical.
- If emphasis or links must be inside a sentence, use a `trans` block with simple
  placeholders.
- Replace manual plural expressions such as
  `face{{ 's' if count != 1 else '' }}` with gettext plural forms.
- Format counts and values before inserting them into display text when the
  visible value needs localized digits, grouping, decimal separators, or
  percent signs.
- Macros should usually accept already translated labels. Feature-specific
  macros may translate fixed copy that belongs to that feature.

## JavaScript conventions

No new hard-coded user-facing strings belong in JavaScript. Add browser copy to
`yaffo/static/locales/en.json`, then sync/translate the other locale catalogs.

Use semantic namespaced keys:

```javascript
i18n.t('utilities:indexPhotos.syncStarted');
i18n.t('common:resultsShown', { count });
```

i18next plural entries use suffixes:

```json
{
  "resultsShown_one": "Showing {{count}} result",
  "resultsShown_other": "Showing {{count}} results"
}
```

Rules:

- Use i18next interpolation. Do not concatenate translated sentence fragments.
- Pass `count` for pluralized messages.
- Use `textContent` for translated plain text.
- If translated HTML seems necessary, prefer constructing DOM nodes explicitly
  rather than assigning arbitrary translated `innerHTML`.
- Do not translate human-readable values received from the server unless the
  contract documents them as enum codes. Server-generated error messages should
  already be localized.
- Keep raw values in `data-*` attributes and format only for display.
- Use the global notification and confirm-dialog components; localize the
  messages passed to them.
- Generated widget templates must receive locale and formatting helpers from
  their host data. Do not hard-code `en-US`.

## Formatting standards

### Dates and times

Use Babel-backed template filters on the server and the i18n service in the
browser. Prefer named widths such as `short` or `medium` over locale-specific
string patterns.

Preserve the timestamp distinction:

- app-generated job/run timestamps are UTC and must be converted to the selected
  user timezone before display;
- `MediaItem.date_taken` is a camera-local wall-clock value with no reliable
  timezone and must be formatted without timezone rebasing.

Do not use fixed `strftime` patterns for user-facing dates or times.

### Numbers and counts

Use localized integer/decimal formatting for visible numbers. Use gettext or
i18next plural rules for surrounding sentences.

Pluralization must not assume only English singular/plural behavior. Tests for
pluralized messages should cover more than `1` when the behavior matters.

### Percentages and confidence values

Use Flask-Babel percent formatting or `i18n.percent()`. Confirm the value
contract before formatting:

- fractions such as `0.95` are formatted directly as `95%`;
- stored percentages such as `95` must be normalized or displayed through a
  contract-specific helper.

Do not append `%` by hand to a localized number.

### Coordinates, units, and sizes

Format coordinate numbers with locale-aware decimal formatting. Keep raw decimal
coordinates in machine-readable payloads.

Distance units are explicit UI/product copy. Localize unit labels and convert
only when a setting or contract says conversion is expected.

Use a dedicated byte-size formatter when displaying file sizes. Do not pass raw
byte counts through a generic decimal formatter without unit handling.

## Catalog workflow

Use Invoke tasks instead of raw `pybabel` commands:

```shell
inv i18n-extract
inv i18n-init --locale=<locale>
inv i18n-update
inv i18n-translate --locale=<locale>
inv i18n-compile
inv i18n-check
```

Task behavior:

- `i18n-extract` updates `messages.pot` from Python and Jinja sources.
- `i18n-init` creates a gettext PO catalog and browser JSON catalog for a new
  locale.
- `i18n-update` merges gettext changes and synchronizes browser catalog keys.
- `i18n-translate` fills missing entries from English source text using the
  configured translation engine, with `--dry-run`, `--keys-only`,
  `--overwrite`, `--batch-size`, and `--engine`.
- `i18n-compile` validates catalogs, then compiles `.po` files to `.mo`.
- `i18n-check` validates catalogs and runs the hard-coded UI text scanner.

Use the stricter release check when a locale must be fully translated:

```shell
inv i18n-check --require-translated
```

Do not edit `.mo` files manually. Edit `.po` and JSON catalogs, then run
`inv i18n-compile`.

Commit these resources together when strings change:

- `messages.pot`
- `yaffo/translations/*/LC_MESSAGES/messages.po`
- `yaffo/translations/*/LC_MESSAGES/messages.mo`
- `yaffo/static/locales/*.json`
- review sidecars such as `*.review.json`, when generated and intentionally
  kept

## Browser catalog shape

Browser catalogs are JSON files with top-level namespaces:

```json
{
  "common": {
    "save": "Save",
    "cancel": "Cancel"
  },
  "media": {
    "favorite": {
      "updateFailed": "Could not update favorite"
    }
  }
}
```

Every non-English browser catalog must have exactly the same leaf keys as
`en.json`. Placeholder names must match the English source for every value.

## Hard-coded text scanner

`inv i18n-check` runs `scripts.i18n_hardcoded` to prevent new untranslated UI
text in templates and JavaScript.

The scanner uses a checked-in fingerprint baseline so historical debt can be
managed deliberately. Existing baseline entries are tolerated, but adding a new
literal or another occurrence of an existing literal fails the check.

Update the baseline only when intentionally accepting new translation debt:

```shell
python -m scripts.i18n_hardcoded --write-baseline
```

The scanner covers visible Jinja text, static `alt`, `aria-label`,
`placeholder`, and `title` attributes, plus JavaScript literals written to DOM
text/HTML properties, notifications, confirmation dialogs, placeholders, and
user-facing fallback strings. It intentionally does not scan every JavaScript
string because selectors, routes, CSS classes, event names, and protocol values
are not translatable UI.

## Translation generation standards

Generated translations must preserve:

- gettext placeholders such as `%(count)s`;
- i18next placeholders such as `{{count}}`;
- HTML tags;
- plural structure;
- message context.

The translation task must validate returned keys and placeholders before
writing catalogs. It must not overwrite non-empty human translations unless
`--overwrite` is explicitly supplied.

Generated translations should remain reviewable. PO entries may be marked fuzzy,
and JSON-generated entries may be tracked in a review sidecar.

## Testing standards

Add focused tests whenever a change introduces or changes translated behavior.
Tests should set locale explicitly and must not depend on the developer
machine's locale or timezone.

Required coverage patterns:

- locale selection priority and unsupported-locale fallback;
- `<html lang>` and `dir`;
- server gettext in templates, flashes, HTMX fragments, and JSON errors;
- singular and plural rendering for relevant count values;
- locale-aware date, decimal, integer, grouping, and percent output;
- UTC job timestamps versus non-rebased camera-local capture dates;
- JavaScript translation lookup, interpolation, pluralization, and fallback;
- JavaScript formatting with the application locale rather than browser
  defaults;
- LLM prompt construction includes the selected application locale and the
  response-language rule;
- catalog key parity and placeholder parity.

Catalog validation tests must report the locale, namespace or message ID,
missing keys, extra keys, and placeholder differences clearly enough to fix the
catalog without reproducing the failure manually.

## LLM-backed features

LLM-backed page, automation, and theme generation receive the selected
application locale in the user turn. The stable system prompt rule is:

> Respond in the language used in the user's latest message; when that language
> is ambiguous, use the application locale.

This governs generated prose only. Fixed application UI copy still belongs in
translation catalogs and must not be delegated to the model.

Do not translate code, identifiers, CSS tokens, query fields, filenames, paths,
or machine-readable values in prompts or generated artifacts.

## Non-goals

Yaffo does not currently require:

- runtime download of translation resources;
- live language switching without a page reload;
- automatic translation of user-entered or AI-authored content;
- automatic unit conversion without an explicit product setting or contract;
- translating logs, persisted identifiers, or filesystem paths.

## References

- [Flask-Babel documentation](https://python-babel.github.io/flask-babel/)
- [Babel date and time formatting](https://babel.pocoo.org/en/latest/dates.html)
- [Babel number formatting](https://babel.pocoo.org/en/latest/numbers.html)
- [i18next pluralization](https://www.i18next.com/translation-function/plurals)
- [i18next formatting](https://www.i18next.com/translation-function/formatting)
- [JavaScript `Intl`](https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Global_Objects/Intl)
