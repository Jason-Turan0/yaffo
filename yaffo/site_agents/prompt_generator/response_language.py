"""The response-language contract shared by every generation agent (page, theme,
automation).

Split into two halves to respect prompt caching: the RULE is constant text and
lives in the stable, cached system prompt; the selected locale is volatile and
lives in the per-request user turn. Keeping them apart means turning the rule on
never invalidates the cached prefix, and switching locale only rewrites the user
turn.
"""
from __future__ import annotations

from yaffo.site_agents.prompt_generator.xml_helpers import block, el


def response_language_block() -> str:
    """Stable system-prompt block: reply in the user's language, falling back to the
    application locale. Constant on purpose so it stays inside the cached prefix; the
    actual locale arrives in the user turn (see `application_locale_el`)."""
    return block("response_language", [
        "Write your chat replies, and any human-readable copy you generate (titles,",
        "labels, captions, summaries), in the language the user wrote their latest",
        "message in.",
        "When that language is ambiguous — an empty, code-only, or single-word message —",
        "use <application_locale> from the user turn.",
        "This governs prose only. Never translate code, identifiers, CSS tokens, query",
        "field names, source names, slugs, or stored data values.",
    ])


def application_locale_el(locale: str) -> str:
    """Volatile user-turn element carrying the selected application locale (e.g. `de`)
    — the fallback the response-language rule points at."""
    return el("application_locale", locale)
