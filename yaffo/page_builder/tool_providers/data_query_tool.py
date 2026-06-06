"""DataQuery tool: lets the agent preview real query results against the photo
library before it writes a widget, so generated widgets are grounded in actual
data (counts, shapes, sample rows) rather than guesses.

A single query is `{ "source": ..., ...filters }` — the same shape a widget's
named queries use and the same resolver the sandbox broker calls.
"""
from __future__ import annotations

import json

from yaffo.page_builder import stub_store
from yaffo.page_builder.tool_providers.tool_provider_types import (
    CallToolReturn,
    RawToolDefinition,
    ToolProvider,
)
from yaffo.page_builder.tool_providers.utils import truncate_tool_result

_SOURCES = ["photos", "persons", "locations", "tags", "stats", "facets"]

_INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "source": {"type": "string", "enum": _SOURCES, "description": "Which data source to query."},
        "location": {"type": "string"},
        "year": {"type": "integer"},
        "date_from": {"type": "string", "description": "ISO date, inclusive."},
        "date_to": {"type": "string", "description": "ISO date, inclusive."},
        "person": {"type": "string", "description": "Single person name to filter photos by."},
        "persons": {"type": "array", "items": {"type": "string"}},
        "tags": {"type": "array", "items": {"type": "string"}},
        "order_by": {"type": "string", "enum": ["date", "random"]},
        "limit": {"type": "integer"},
    },
    "required": ["source"],
    "additionalProperties": False,
}

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
        source = args.get("source")
        if source not in _SOURCES:
            return f"Unknown source '{source}'. Valid sources: {', '.join(_SOURCES)}."
        data = stub_store.resolve_query(args)
        return truncate_tool_result(_preview(source, data))


def _preview(source: str, data) -> str:
    if isinstance(data, list):
        payload = {"source": source, "count": len(data), "sample": data[:_SAMPLE_SIZE]}
    else:
        payload = {"source": source, "data": data}
    return json.dumps(payload, indent=2)