"""Widget tools: create_widget and update_widget.

Tools the agent calls to draft or edit widgets on a page. The provider is scoped
to a single page_id at construction, so the model cannot target another page — it
only supplies content.

These tools do **not** persist: generation is non-destructive. Each call produces
a `WidgetDraft` that is streamed to the browser and held there until the user
clicks Save (which is the only thing that writes to the store). Within one run the
provider keeps the drafts in memory so the model can create a widget and then
update it, and so edits to an already-saved widget merge onto its current content.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

from yaffo.page_builder import stub_store
from yaffo.page_builder.tool_providers.tool_provider_types import (
    CallToolReturn,
    RawToolDefinition,
    ToolProvider,
    ToolResult,
)


@dataclass
class WidgetDraft:
    """The widget schema sent to the browser (distinct from the tool_result text
    sent to the model). Mirrors the persisted widget's content + suggested size."""
    id: str
    title: str
    data_query: dict = field(default_factory=dict)
    html: str = ""
    css: str = ""
    js: str = ""
    grid_w: int = 4
    grid_h: int = 3
    state: dict = field(default_factory=dict)

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
        # Drafts touched this run, keyed by widget id. Not persisted — they seed
        # in-run updates and are streamed to the browser for Save to commit.
        self._drafts: dict[str, WidgetDraft] = {}

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
                    "properties": {"widget_id": {"type": "string"}, **_CONTENT_PROPS},
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

    def _create(self, args: dict) -> ToolResult:
        draft = WidgetDraft(
            id=stub_store.new_widget_id(),
            title=args.get("title") or "Untitled widget",
            data_query=args.get("data_query") or {},
            html=args.get("html", ""),
            css=args.get("css", ""),
            js=args.get("js", ""),
            grid_w=int(args.get("grid_w", 4)),
            grid_h=int(args.get("grid_h", 3)),
        )
        self._drafts[draft.id] = draft
        return ToolResult(
            model_text=f'Created widget {draft.id} "{draft.title}".',
            host_data=asdict(draft),
        )

    def _update(self, args: dict) -> CallToolReturn:
        widget_id = args.get("widget_id")
        if not widget_id:
            return "update_widget requires widget_id."
        # Merge onto the draft if we created/edited it this run, else onto the
        # widget's currently-saved content. Reading the store is not persisting.
        draft = self._drafts.get(widget_id) or self._load_saved(widget_id)
        if draft is None:
            return f"Widget {widget_id} not found on page {self.page_id}."
        changed = [k for k in _CONTENT_FIELDS if args.get(k) is not None]
        for key in changed:
            setattr(draft, key, args[key])
        self._drafts[draft.id] = draft
        return ToolResult(
            model_text=f'Updated widget {draft.id} "{draft.title}" (changed: {", ".join(changed) or "nothing"}).',
            host_data=asdict(draft),
        )

    def _load_saved(self, widget_id: str) -> "WidgetDraft | None":
        page = stub_store.get_page(self.page_id)
        widget = next((w for w in page.widgets if w.id == widget_id), None) if page else None
        if widget is None:
            return None
        return WidgetDraft(
            id=widget.id,
            title=widget.title,
            data_query=widget.data_query,
            html=widget.html,
            css=widget.css,
            js=widget.js,
            grid_w=widget.grid_w,
            grid_h=widget.grid_h,
            state=widget.state,
        )