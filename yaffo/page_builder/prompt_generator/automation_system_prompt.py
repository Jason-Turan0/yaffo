"""Builds the automation-builder agent's system prompt: the stable contract the
model writes a Starlark automation against, in XML-delimited sections.

Keep this content STABLE — it's the cached prefix sent on every generation. The
per-automation slug lives on the slug-scoped write_automation_code tool, not here,
so the cache holds across every automation. The host API and data sources are
derived from their single sources (render_host_api / FIELDS_BY_SOURCE), so this
can't drift from what the sandbox and resolver actually provide.
"""
from __future__ import annotations

from yaffo.background_tasks.automation_sandbox.automation_host import render_host_api
from yaffo.db.models import EVENTS
from yaffo.db.repositories.data_query_repository import FIELDS_BY_SOURCE
from yaffo.page_builder.prompt_generator.xml_helpers import block


def _role() -> str:
    return block("role", [
        "You write small automations for a personal photo-organization app.",
        "An automation is a Starlark script that runs on a schedule or when a domain",
        "event fires (e.g. photos finished indexing). Given a request, you write the",
        "script that does what the user wants.",
    ])


def _language() -> str:
    return block("language", [
        "The script is Starlark — Python-like, but deterministic and sandboxed:",
        "- for/if and comprehensions are fine (at top level too); no while loops and no",
        "  recursion.",
        "- No imports, no file/network I/O, no access to anything outside the host API.",
        "- print(...) logs a line (captured into the run's output).",
        "- The trailing expression is the script's value; most automations just act via",
        "  the host API and print, returning nothing.",
        "Save your work with write_automation_code. It parse-checks the code and reports",
        "any error to fix; the draft isn't stored until it parses.",
    ])


def _context() -> str:
    return block("context", [
        "Your script is given a `ctx` dict describing what triggered the run:",
        "- ctx['event_type']: the event name for an event run, or None for a schedule.",
        "- ctx['job_id']: the id of the job that emitted the event (or None).",
        "- ctx['photo_ids']: the photo ids the event concerns (empty for a schedule).",
        "Read ctx to act on exactly what triggered the run (e.g. the photos just indexed).",
    ])


def _host_api() -> str:
    return block("host_api", [
        "The host API — the only functions your script may call to reach app data:",
        "",
        render_host_api(),
    ])


def _sources() -> str:
    lines = ", ".join(
        f"{src} ({', '.join(fields)})" for src, fields in FIELDS_BY_SOURCE.items()
    )
    return block("data_sources", [
        "Sources data_query can read (each a table; a query returns its rows as column",
        "dicts, or an aggregate):",
        lines,
        'Filter columns with operators at the top level, e.g. {"source": "photos",',
        '"year": {"eq": 2024}, "id": {"in": [1,2,3]}, "limit": 24}. Operators: eq, ne,',
        "lt, lte, gt, gte, contains, in.",
    ])


def _events() -> str:
    names = ", ".join(f"{key} ({label})" for key, label in EVENTS.items())
    return block("events", [
        "Events an automation can be triggered by (the trigger is configured separately;",
        "you only write the script that reacts):",
        names,
    ])


def _conventions() -> str:
    return block("conventions", [
        "- Use ctx to scope the work to what triggered the run when it makes sense.",
        "- Keep it small and readable; one write_automation_code call with the finished",
        "  script, then a short summary of what it does. Call again to refine.",
    ])


def build_automation_builder_system_prompt() -> str:
    """Assemble the stable, XML-delimited automation-builder system prompt.
    Parameterless — the per-automation slug lives on the tool, so this prefix stays
    cacheable across every automation."""
    return "\n\n".join([
        _role(),
        _language(),
        _context(),
        _host_api(),
        _sources(),
        _events(),
        _conventions(),
    ])
