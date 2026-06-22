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
from yaffo.background_tasks.automation_sandbox.executor import context_globals
from yaffo.db.models import EVENTS
from yaffo.db.repositories.data_query_repository import FIELDS_BY_SOURCE
from yaffo.site_agents.prompt_generator.source_catalog import (
    calculated_filter_lines,
    relationship_summary,
    virtual_source_lines,
)
from yaffo.site_agents.prompt_generator.xml_helpers import block


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


# Prose for each ctx field. The field *set* is introspected from context_globals (the
# real ctx shape the sandbox injects), so this can't advertise a field a script won't
# get, or silently miss a new one — only the wording lives here.
_CTX_FIELD_DOCS = {
    "event_type": "the event name for an event run, or None for a schedule.",
    "job_id": "the id of the job that emitted the event (or None).",
    "media_item_ids": "the media ids the event concerns (empty for a schedule).",
    "groups": (
        "related-media groupings — one list of media ids per group (e.g. each "
        "duplicate set, keeper first); empty for events without groupings."
    ),
}


def _context() -> str:
    fields = [
        f"- ctx['{key}']: {_CTX_FIELD_DOCS.get(key, 'see the events catalog.')}"
        for key in context_globals(None)
    ]
    return block("context", [
        "Your script is given a `ctx` dict describing what triggered the run:",
        *fields,
        "Read ctx to act on exactly what triggered the run (e.g. the photos just",
        "indexed) — see <scoping>.",
    ])


def _scoping() -> str:
    return block("scoping", [
        "Scope an event run to its photos — never sweep the whole library on an event.",
        "When ctx['event_type'] is set, the run is about exactly ctx['media_item_ids'] (the",
        "photos that event concerns), so every photos query MUST filter to them:",
        '  data_query({"source": "media_items", "id": {"in": ctx["media_item_ids"]}, ...})',
        "and derive faces/people/labels from that set. Querying photos without an `id`",
        "filter on an event run re-scans the entire library on every event — that is the",
        "wrong behaviour: slow, and it acts on photos the event never mentioned.",
        "If ctx['media_item_ids'] is empty on an event run, there's nothing to do — stop.",
        "Operate library-wide ONLY on a schedule run (ctx['event_type'] is None and",
        "ctx['media_item_ids'] is empty) — that's the one case where you query without an id",
        "filter.",
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
        "Table sources data_query can read (a query returns rows as column dicts, or an",
        "aggregate). The columns shown are the filterable/queryable columns:",
        lines,
        "There are no joins — run a query per source and stitch the rows together on the",
        f"foreign-key columns: {relationship_summary()}.",
        "(photo_labels + classification_labels are the auto-classifier's labels — read-only;",
        "to categorize photos yourself, write tags with tag_media_items.)",
        'Filter columns with operators at the top level, e.g. {"source": "media_items",',
        '"year": {"eq": 2024}, "id": {"in": [1,2,3]}, "limit": 24}. Operators: eq, ne,',
        "lt, lte, gt, gte, contains, in, prefix (path columns: matches a leading prefix / subtree).",
        "Some tables also accept host-derived filter columns (disk paths never exposed; a",
        "photo's media_dir_id / relative_path, which move_media_item also addresses by):",
        *calculated_filter_lines(FIELDS_BY_SOURCE),
        "Computed (non-table) sources take the params shown (not column filters):",
        *virtual_source_lines(),
        "Call get_source_schema(source) for a source's full returned-row fields.",
    ])


def _events() -> str:
    names = ", ".join(f"{key} ({label})" for key, label in EVENTS.items())
    return block("events", [
        "Domain events an automation can be triggered by (add one with",
        "add_automation_trigger; you write the script that reacts to it):",
        names,
    ])


def _triggers() -> str:
    return block("triggers", [
        "Decide when the automation should run and add the matching trigger with",
        "add_automation_trigger:",
        "- A schedule trigger runs on a 5-field cron (e.g. '0 3 * * *' = daily at 3am).",
        "- An event trigger runs when a domain event fires (see <events>); the run's",
        "  ctx names the photos that event concerns.",
        "Pick the trigger that fits the request — recurring upkeep wants a schedule, a",
        "react-to-new-photos task wants an event. An automation may have several. Drop",
        "one you no longer want with remove_automation_trigger (identified the same way",
        "— trigger_type plus its cron / event_type). The user can still adjust triggers",
        "in the UI afterward, so set up what's clearly intended and don't ask.",
    ])


def _batching() -> str:
    return block("batching", [
        "Writes are batched. Every mutating host function (tag_media_items, assign_faces,",
        "move_media_items, rename_files) takes a LIST and persists the whole set in one",
        "transaction — there are no single-item write functions. So build the work into",
        "a list as you loop, then make ONE write call with the full list; never call a",
        "write inside a loop. Each takes a list of dicts (see the host API for each",
        "one's keys). Reads (data_query, match_people, face_similarity) can still run",
        "per item in the loop that builds the batch.",
        "Example — tag everything the run touched, in one write:",
        '  to_tag = [{"media_item_id": pid, "name": "reviewed"} for pid in ctx["media_item_ids"]]',
        "  tag_media_items(to_tag)",
    ])


def _progress() -> str:
    return block("progress", [
        "Report progress on long runs. If the script loops over many items (e.g. the",
        "photos in ctx, or a large data_query result), call report_progress(done,",
        "total) as you go — once per chunk or every several items — so the run history",
        "shows a live percentage instead of just spinning. `total` is the full count,",
        "`done` is how many you've handled so far; a final report_progress(total, total)",
        "marks it complete. Skip it for trivial, near-instant runs.",
        "Example — report while building the batch, then write once:",
        '  pids = ctx["media_item_ids"]',
        "  to_tag = []",
        "  for i, pid in enumerate(pids):",
        '      to_tag.append({"media_item_id": pid, "name": "reviewed"})',
        "      report_progress(i + 1, len(pids))",
        "  tag_media_items(to_tag)",
    ])


def _conventions() -> str:
    return block("conventions", [
        "- Scope work to ctx on an event run (see <scoping>); go library-wide only on a",
        "  schedule run.",
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
        _scoping(),
        _host_api(),
        _sources(),
        _events(),
        _triggers(),
        _batching(),
        _progress(),
        _conventions(),
    ])
