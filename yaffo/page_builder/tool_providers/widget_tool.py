"""Widget write tools: create_widget and update_widget.

Server-side tools the agent calls to add or edit widgets on a page. The provider
is scoped to a single page_id at construction, so the model cannot target another
page — it only supplies content.
"""
from __future__ import annotations

from yaffo.page_builder import stub_store
from yaffo.page_builder.tool_providers.tool_provider_types import (
    CallToolReturn,
    RawToolDefinition,
    ToolProvider,
)

_SOURCES = ["photos", "persons", "locations", "tags", "stats", "facets"]

# A single query: a source plus filters (same shape as the DataQuery tool).
_QUERY_SCHEMA = {
    "type": "object",
    "properties": {
        "source": {"type": "string", "enum": _SOURCES},
        "location": {"type": "string"},
        "year": {"type": "integer"},
        "date_from": {"type": "string"},
        "date_to": {"type": "string"},
        "person": {"type": "string"},
        "persons": {"type": "array", "items": {"type": "string"}},
        "tags": {"type": "array", "items": {"type": "string"}},
        "order_by": {"type": "string", "enum": ["date", "random"]},
        "limit": {"type": "integer"},
    },
    "required": ["source"],
    "additionalProperties": False,
}

# data_query is a dict of NAMED queries: { "<your_name>": <query> }.
_DATA_QUERY_SCHEMA = {
    "type": "object",
    "description": "Named queries: each key is a query name you choose; each value is one query.",
    "additionalProperties": _QUERY_SCHEMA,
}

_CONTENT_PROPS = {
    "title": {"type": "string", "description": "Short, human title for the widget."},
    "data_query": _DATA_QUERY_SCHEMA,
    "html": {"type": "string"},
    "css": {"type": "string"},
    "js": {"type": "string"},
    "grid_w": {"type": "integer", "description": "Width in grid columns (1-12)."},
    "grid_h": {"type": "integer", "description": "Height in grid rows."},
}

_CONTENT_FIELDS = ("title", "data_query", "html", "css", "js", "grid_w", "grid_h")


class WidgetToolProvider(ToolProvider):
    CREATE = "create_widget"
    UPDATE = "update_widget"

    def __init__(self, page_id: int):
        self.page_id = page_id

    def get_tools(self) -> list[RawToolDefinition]:
        return [
            RawToolDefinition(
                name=self.CREATE,
                description=(
                    "Add a new widget to the page. Supply its title, data_query (named queries), "
                    "and html/css/js. It is placed at the bottom of the layout."
                ),
                input_schema={
                    "type": "object",
                    "properties": _CONTENT_PROPS,
                    "required": ["title", "data_query", "html"],
                    "additionalProperties": False,
                },
            ),
            RawToolDefinition(
                name=self.UPDATE,
                description=(
                    "Edit an existing widget by id. Provide only the fields you want to change; "
                    "omitted fields are left as-is."
                ),
                input_schema={
                    "type": "object",
                    "properties": {"widget_id": {"type": "integer"}, **_CONTENT_PROPS},
                    "required": ["widget_id"],
                    "additionalProperties": False,
                },
            ),
        ]

    def call_tool(self, name: str, args: dict) -> CallToolReturn:
        args = args or {}
        if name == self.CREATE:
            return self._create(args)
        if name == self.UPDATE:
            return self._update(args)
        return f"Unknown tool: {name}"

    def _create(self, args: dict) -> str:
        widget = stub_store.create_widget(
            self.page_id,
            title=args.get("title") or "Untitled widget",
            data_query=args.get("data_query") or {},
            html=args.get("html", ""),
            css=args.get("css", ""),
            js=args.get("js", ""),
            grid_w=int(args.get("grid_w", 4)),
            grid_h=int(args.get("grid_h", 3)),
        )
        if widget is None:
            return f"Page {self.page_id} not found."
        return f'Created widget {widget.id} "{widget.title}".'

    def _update(self, args: dict) -> str:
        widget_id = args.get("widget_id")
        if widget_id is None:
            return "update_widget requires widget_id."
        fields = {k: args[k] for k in _CONTENT_FIELDS if args.get(k) is not None}
        widget = stub_store.update_widget(self.page_id, int(widget_id), **fields)
        if widget is None:
            return f"Widget {widget_id} not found on page {self.page_id}."
        changed = ", ".join(fields) or "nothing"
        return f'Updated widget {widget.id} "{widget.title}" (changed: {changed}).'