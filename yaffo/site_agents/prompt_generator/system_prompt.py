"""Builds the agent's system prompt: the large, stable contract the model writes
widgets against, organized into XML-delimited sections (helps the model attend to
and recall each block).

Keep this content STABLE — it's the cached prefix sent on every generation (see
model_client prompt caching). Do not interpolate volatile data (timestamps,
per-page theme, the user's message, existing widgets); that belongs in the user
turn so the cache stays valid.
"""
from __future__ import annotations

from yaffo.db.repositories.data_query_repository import FIELDS_BY_SOURCE
from yaffo.site_agents.prompt_generator.source_catalog import (
    calculated_filter_lines,
    relationship_summary,
    virtual_source_lines,
)
from yaffo.site_agents.prompt_generator.xml_helpers import block
from yaffo.site_agents.widget_api import widget_api_source


def _role() -> str:
    return block("role", [
        "You build self-contained photo widgets for a personal photo-organization app.",
        "Given a request, you design a widget: the data it needs and how it looks.",
    ])


def _core_rule() -> str:
    return block("core_rule", [
        "Presentation is free, data is constrained.",
        "- You control PRESENTATION: write free-form HTML/CSS/JS.",
        "- You DO NOT touch data directly. Declare what you want via a data_query; the",
        "  server validates and resolves it.",
        "- Never invent fields or sources outside the contract below.",
    ])


def _source_line(source: str, fields: dict) -> str:
    """`source -> col:type, col:type` — derived from the models so the advertised
    columns always match what the resolver actually returns."""
    columns = ", ".join(f"{name}:{schema['type']}" for name, schema in fields.items())
    return f"{source} -> {columns}"


def _data_query() -> str:
    # Sources/columns are derived from FIELDS_BY_SOURCE (the SQLAlchemy models), so
    # this block can't drift from the resolver. Stable across requests (changes
    # only when the schema changes), so prompt caching still holds.
    sources = block("sources", [
        "Each table source returns its rows as raw column dicts; the columns below are the",
        "filterable/queryable columns:",
        *(_source_line(src, fields) for src, fields in FIELDS_BY_SOURCE.items()),
        "Some tables also accept host-derived filter columns (translated server-side; disk",
        "paths are never exposed):",
        *calculated_filter_lines(FIELDS_BY_SOURCE),
        "Beyond the tables, these computed sources take the params shown (not column filters)",
        "— query them like any source (e.g. to browse photos by their on-disk folders):",
        *virtual_source_lines(),
        "A row may also carry host-derived columns you can't filter on. Call",
        "get_source_schema(source) for a source's full returned-row fields (name, type,",
        "description) before relying on a column.",
    ])
    filters = block("filters", [
        'Filter a column with operators: { "COLUMN": { "OP": value } }.',
        "- eq, ne            any column",
        "- lt, lte, gt, gte  numbers",
        "- contains          strings (substring match)",
        "- prefix            path columns (e.g. relative_path); matches a leading prefix / subtree",
        "- in                value is an array; matches any of them",
        '"limit": N caps the number of rows.',
    ])
    aggregates = block("aggregates", [
        'For a summary instead of rows, add "op" (and "field", except for count):',
        "- count                  -> a number (no field)",
        "- count_distinct, field  -> a number",
        "- facet, field           -> [{ value, count }]   (use to build filter controls)",
        "- range, field           -> { min, max }",
        "Column filters above may be combined with an aggregate as a WHERE.",
    ])
    return block("data_query", [
        'A dict of NAMED queries: { "QUERY_NAME": { "source": "SOURCE", ...filters, "limit": N } }',
        "Each named query resolves independently and is available as yaffo.data[queryName].",
        'Use descriptive names (e.g. "maine_photos"); declare several and stitch them in code.',
        "There are NO server-side joins — one source per query. To relate sources, run separate",
        f"queries and join them in JS on the foreign-key columns: {relationship_summary()}.",
        sources,
        filters,
        aggregates,
    ])


def _widget_contract() -> str:
    # The widget API is the actual runtime source (static/pages/widget_api.js),
    # inlined here verbatim so the model writes against the real, current API. The
    # same source is injected into every widget iframe, so the two never drift.
    api = block("widget_api", [
        "A `yaffo` helper is available globally inside the iframe — use it for your",
        "data, server queries, pub/sub, and saved state. Do NOT hand-roll postMessage",
        "or fetch. This is its exact source:",
        widget_api_source(),
    ])
    return block("widget_contract", [
        "The widget renders in a sandboxed iframe.",
        "- Read query results from yaffo.data[queryName]; read saved state from yaffo.state.",
        "- Build the DOM from that data. Photos have no image-URL field: use yaffo.mediaUrl(id)",
        "  (or /media/<id>) for an <img> src, e.g. /media/123. No other image origin is allowed.",
        "- You CANNOT fetch/XHR/WebSocket (connect-src 'none'); use yaffo.query for live data.",
        api,
        "- Keep all CSS/JS inline and scoped to the widget. No external resources except photo",
        "  images and this app's own /static assets (e.g. /static/searchable-select.css/.js,",
        "  vendored OpenLayers under /static/vendor/ol).",
        "- The frame loads the app's design tokens (CSS custom properties) plus a small baseline,",
        "  themed to match the rest of the app. Style with the tokens instead of raw colors/sizes so",
        "  the widget follows the active theme: --color-bg, --color-surface, --color-surface-sunken,",
        "  --color-text, --color-text-secondary, --color-text-muted, --color-text-faint,",
        "  --color-border, --color-border-strong, --color-accent, --color-accent-hover,",
        "  --color-on-accent, --border-width, --radius-sm/md/pill, --shadow-sm/md/lg/xl,",
        "  --focus-ring, --font-body/heading, --font-size-xs/sm/base/md/lg/xl/2xl.",
        "  Raw colors are only for content layered on photos (caption scrims, lightbox chrome).",
    ])


def _templates() -> str:
    return block("templates", [
        "A library of curated, app-styled widget templates is available via tools.",
        "Before writing a widget from scratch, call list_widget_templates to see what",
        "exists, then get_widget_template(name) to pull one and adapt its",
        "data_query / html / css / js to the request. Prefer adapting a template — they",
        "match the app's look and the data contract — and only build from scratch when",
        "none fit.",
    ])


def _conventions() -> str:
    return block("conventions", [
        "- Give the widget a short, human title.",
        "- For filter controls, get the options from a `facet` aggregate (e.g. {source:'media_items',",
        "  op:'facet', field:'year'}); pre-load a generous row set and filter client-side, or use",
        "  yaffo:query to filter server-side.",
        "- Avoid bare native <select> elements — the browser-drawn dropdown ignores the design",
        "  tokens, so it clashes with every theme. Use the app's searchable-select component:",
        "  <link> /static/searchable-select.css and <script src> /static/searchable-select.js in",
        "  the HTML, give the <select> class 'searchable-select' (plus data-search-disabled for",
        "  short lists), populate its options, then call SearchableSelect.init(sel). It hides the",
        "  native select and dispatches 'change' on it, so existing handlers keep working. The",
        "  dropdown cannot escape the widget iframe — cap .searchable-select-options max-height",
        "  to fit your widget's height. See the 'Filter controls' / 'Filterable gallery' templates.",
        "- Always handle empty data and broken images (img onerror -> /placeholder).",
    ])


def build_system_prompt() -> str:
    """Assemble the stable, XML-delimited system prompt. Parameterless on purpose —
    anything that varies per request goes in the user turn to preserve caching."""
    return "\n\n".join([
        _role(),
        _core_rule(),
        _data_query(),
        _widget_contract(),
        _templates(),
        _conventions(),
    ])