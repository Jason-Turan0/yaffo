"""DataQuery tool: lets the agent preview real query results against the photo
library before it writes a widget, so generated widgets are grounded in actual
data (counts, shapes, sample rows) rather than guesses.

A single query is `{ "source": ..., ...filters }` — the same shape a widget's
named queries use and the same resolver the sandbox broker calls.
"""
from __future__ import annotations

import json

from sqlalchemy.orm import Session

from yaffo.background_tasks.automation_sandbox.media_dirs import enrich_photo_rows
from yaffo.db.repositories.data_query_repository import (
    AGGREGATE_OPS,
    SOURCES,
    resolve_query,
    source_schema,
)
from yaffo.site_agents.tool_providers.tool_provider_types import (
    CallToolReturn,
    RawToolDefinition,
    ToolProvider,
)
from yaffo.site_agents.tool_providers.utils import truncate_tool_result

# Anthropic forbids oneOf/allOf/anyOf at a tool input_schema's top level, so this
# preview tool advertises a single permissive object: the source, the aggregate
# directives, and limit. Per-column filters ({ "column": { "op": value } }) ride
# along as extra properties and are validated server-side by resolve_query, which
# returns a precise error the model can correct from. (The strict, per-source
# contract lives in the system prompt and data_query_repository.)
_INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "source": {"type": "string", "enum": list(SOURCES), "description": "Which table to query."},
        "op": {"enum": list(AGGREGATE_OPS), "description": "Aggregate op; omit to return rows."},
        "field": {"type": "string", "description": "Column the aggregate operates on (omit for count)."},
        "limit": {"type": "integer", "description": "Max rows to return (rows queries only)."},
    },
    "required": ["source"],
    "additionalProperties": True,
}

_SCHEMA_INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "source": {"type": "string", "enum": list(SOURCES), "description": "Which table to describe."},
    },
    "required": ["source"],
    "additionalProperties": False,
}

_SAMPLE_SIZE = 5


class DataQueryToolProvider(ToolProvider):
    RUN_QUERY_TOOL = "run_data_query"
    SOURCE_SCHEMA_TOOL = "get_source_schema"

    def __init__(self, session: Session):
        # Injected so the provider works under either a request's db.session or a
        # background worker's SessionFactory session (no global db.session).
        self.session = session

    def get_tools(self) -> list[RawToolDefinition]:
        return [
            RawToolDefinition(
                name=self.RUN_QUERY_TOOL,
                description=(
                    "Run a data query against the photo library to preview real results before "
                    "writing a widget. Provide a single query: a `source` plus filters. Returns a "
                    "row count and a small sample in that source's shape. Use it to confirm a "
                    "filter returns data and to see the exact fields available."
                ),
                input_schema=_INPUT_SCHEMA,
            ),
            RawToolDefinition(
                name=self.SOURCE_SCHEMA_TOOL,
                description=(
                    "Describe a source table: returns the fields (name, type, description) a row "
                    "of that source has, including calculated columns the host appends. Use it to "
                    "see exactly which fields are available before writing a query or a widget."
                ),
                input_schema=_SCHEMA_INPUT_SCHEMA,
            ),
        ]

    def call_tool(self, name: str, args: dict) -> CallToolReturn:
        args = args or {}
        if name == self.RUN_QUERY_TOOL:
            return self._run_query(args)
        if name == self.SOURCE_SCHEMA_TOOL:
            return self._source_schema(args)
        return f"Unknown tool: {name}"

    def _run_query(self, args: dict) -> CallToolReturn:
        # The resolver validates the query; an invalid one is fed back to the model
        # (with the precise error) so it can correct the source/filters.
        try:
            rows = resolve_query(self.session, args)
            if args.get("source") == "photos" and isinstance(rows, list):
                enrich_photo_rows(self.session, rows)
        except ValueError as exc:
            return f"Invalid query: {exc}"
        return truncate_tool_result(_preview(args.get("source"), rows))

    def _source_schema(self, args: dict) -> CallToolReturn:
        source = args.get("source")
        try:
            fields = source_schema(source)
        except ValueError as exc:
            return f"Invalid source: {exc}"
        return truncate_tool_result(json.dumps({"source": source, "fields": fields}, indent=2))


def _preview(source: str, data) -> str:
    if isinstance(data, list):
        payload = {"source": source, "count": len(data), "sample": data[:_SAMPLE_SIZE]}
    else:
        payload = {"source": source, "data": data}
    return json.dumps(payload, indent=2)