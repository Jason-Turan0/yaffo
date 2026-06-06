"""Builds the agent's system prompt: the large, stable contract the model writes
widgets against, organized into XML-delimited sections (helps the model attend to
and recall each block).

Keep this content STABLE — it's the cached prefix sent on every generation (see
model_client prompt caching). Do not interpolate volatile data (timestamps,
per-page theme, the user's message, existing widgets); that belongs in the user
turn so the cache stays valid.
"""
from __future__ import annotations

from yaffo.page_builder.prompt_generator.xml_helpers import block, el


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


def _data_query() -> str:
    # TODO Draft instructions. Revisit once data query lib is created. Want an explicit
    # schema that would be validated at runtime.
    sources = block("sources", [
        "photos    -> [{ id, url, thumb_url, taken_at, year, location, persons[], tags[], width, height }]",
        "persons   -> [{ name, photo_count, face_thumb_url }]",
        "locations -> [{ name, lat, lon, count }]",
        "tags      -> [{ name, count }]",
        "stats     -> { photos, people, locations, years }",
        "facets    -> { years[], tags[], locations[], persons[] }   (for in-widget filter controls)",
    ])
    return block("data_query", [
        'A dict of NAMED queries: { "QUERY_NAME": { "source": "SOURCE", ...filters } }',
        "Each named query resolves independently and is injected as window.__DATA__[queryName].",
        'Use descriptive names (e.g. "maine_photos"); a widget may declare several queries and',
        "stitch the results together in its code.",
        sources,
        el("photo_filters", "location, year, date_from, date_to, persons[], tags[], person, order_by, limit"),
    ])


def _widget_contract() -> str:
    # TODO draft instructions. Want an official API spec in javascript that would be sent
    # to the model. Would be read from the source code so is always up to date.
    messaging = block("messaging", [
        "For anything beyond a one-time render, postMessage to the parent:",
        "yaffo:query   {requestId, query}   -> live server query; reply arrives as yaffo:result",
        "yaffo:publish {topic, payload}     -> broadcast to other widgets (pub/sub)",
        "yaffo:event   {topic, payload}     -> received from the bus",
        "yaffo:state   {state}              -> persist this widget's state",
    ])
    return block("widget_contract", [
        "The widget renders in a sandboxed iframe.",
        "- Read data from window.__DATA__[queryName] and window.__STATE__ (persisted widget state).",
        "- Build the DOM from that data; reference photo images by `url` (e.g. /photos/123).",
        "- The frame CANNOT fetch/XHR/WebSocket (connect-src 'none').",
        messaging,
        "- Keep all CSS/JS inline and scoped to the widget. No external resources except photo images.",
    ])


def _conventions() -> str:
    return block("conventions", [
        "- Give the widget a short, human title.",
        "- Pre-load a generous set + facets when filtering client-side; use yaffo:query when filtering",
        "  server-side.",
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
        _conventions(),
    ])