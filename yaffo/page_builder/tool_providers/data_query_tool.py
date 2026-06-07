"""DataQuery tool: lets the agent preview real query results against the photo
library before it writes a widget, so generated widgets are grounded in actual
data (counts, shapes, sample rows) rather than guesses.

A single query is `{ "source": ..., ...filters }` — the same shape a widget's
named queries use and the same resolver the sandbox broker calls.
"""
from __future__ import annotations

import json

from yaffo.db import db
from yaffo.db.repositories.data_query_repository import QUERY_SCHEMA, resolve_query
from yaffo.page_builder.tool_providers.tool_provider_types import (
    CallToolReturn,
    RawToolDefinition,
    ToolProvider,
)
from yaffo.page_builder.tool_providers.utils import truncate_tool_result

# A single query, validated by the same contract the resolver and the sandbox
# broker use (minus the top-level $schema meta key, which only belongs at a
# document root, not on an embedded tool input_schema).
_INPUT_SCHEMA = {k: v for k, v in QUERY_SCHEMA.items() if k != "$schema"}

_SAMPLE_SIZE = 5


class DataQueryToolProvider(ToolProvider):
    TOOL_NAME = "run_data_query"

    def get_tools(self) -> list[RawToolDefinition]:
        return [
            RawToolDefinition(
                name=self.TOOL_NAME,
                description=(
                    "Run a data query against the photo library to preview real results before "
                    "writing a widget. Provide a single query: a `source` plus filters. Returns a "
                    "row count and a small sample in that source's shape. Use it to confirm a "
                    "filter returns data and to see the exact fields available."
                ),
                input_schema=_INPUT_SCHEMA,
            )
        ]

    def call_tool(self, name: str, args: dict) -> CallToolReturn:
        if name != self.TOOL_NAME:
            return f"Unknown tool: {name}"
        args = args or {}
        # The resolver validates the query; an invalid one is fed back to the model
        # (with the precise error) so it can correct the source/filters.
        try:
            data = resolve_query(db.session, args)
        except ValueError as exc:
            return f"Invalid query: {exc}"
        return truncate_tool_result(_preview(args.get("source"), data))


def _preview(source: str, data) -> str:
    if isinstance(data, list):
        payload = {"source": source, "count": len(data), "sample": data[:_SAMPLE_SIZE]}
    else:
        payload = {"source": source, "data": data}
    return json.dumps(payload, indent=2)