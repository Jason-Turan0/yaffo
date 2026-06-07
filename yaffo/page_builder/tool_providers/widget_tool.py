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

import dataclasses
from dataclasses import asdict, dataclass, field
from typing import Optional, get_args, get_type_hints

from yaffo.db import db
from yaffo.db.repositories import custom_page_repository as page_repo
from yaffo.db.repositories.data_query_repository import DATA_QUERY_SCHEMA
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
    grid_x: Optional[int] = None  # None = no explicit placement (bottom on create, keep on edit)
    grid_y: Optional[int] = None
    grid_w: int = 4
    grid_h: int = 3
    state: dict = field(default_factory=dict)

# WidgetDraft is the single source of truth for a widget's content fields: both
# the model-facing tool schema (_CONTENT_PROPS) and the editable-field list
# (_CONTENT_FIELDS) are derived from it, so adding a content field there updates
# the tools automatically.
#
# Two of its fields are not model-supplied content: `id` is minted server-side and
# `state` is widget-authored runtime state, so neither is offered to the model.
_NON_CONTENT_FIELDS = {"id", "state"}

# dataclass annotation -> JSON Schema type for the scalar content fields. Optional
# fields (grid_x/grid_y) are unwrapped to their inner type.
_JSON_TYPE = {str: "string", int: "integer", dict: "object"}

# Descriptions for the fields that warrant one (others are self-explanatory).
_FIELD_DESCRIPTIONS = {
    "title": "Short, human title for the widget.",
    "grid_w": "Width in grid columns (1-12).",
    "grid_h": "Height in grid rows.",
    "grid_x": "Column position (0-based, 0-11). Omit to drop a new widget at the bottom of the page, or to leave an edited widget where it is.",
    "grid_y": "Row position (0-based). Omit to drop a new widget at the bottom of the page, or to leave an edited widget where it is.",
}

# Per-field schema overrides. data_query reuses the validated named-query contract
# from the data-query repository, so the tool advertises exactly what the resolver
# accepts (minus the top-level $schema meta key, which only belongs at a document
# root, not on an embedded sub-schema).
_FIELD_SCHEMA_OVERRIDES = {
    "data_query": {k: v for k, v in DATA_QUERY_SCHEMA.items() if k != "$schema"},
}

_FIELD_TYPES = get_type_hints(WidgetDraft)


def _json_schema_type(annotation) -> str:
    """Map a field annotation to a JSON Schema type, unwrapping Optional[X]."""
    inner = [a for a in get_args(annotation) if a is not type(None)]
    return _JSON_TYPE[inner[0] if inner else annotation]


def _content_field_schema(name: str) -> dict:
    if name in _FIELD_SCHEMA_OVERRIDES:
        return _FIELD_SCHEMA_OVERRIDES[name]
    schema = {"type": _json_schema_type(_FIELD_TYPES[name])}
    if name in _FIELD_DESCRIPTIONS:
        schema["description"] = _FIELD_DESCRIPTIONS[name]
    return schema


_CONTENT_FIELDS = tuple(
    f.name for f in dataclasses.fields(WidgetDraft) if f.name not in _NON_CONTENT_FIELDS
)
_CONTENT_PROPS = {name: _content_field_schema(name) for name in _CONTENT_FIELDS}


def _opt_int(value) -> Optional[int]:
    """Coerce an optional grid coordinate: an int when supplied, else None (no
    explicit placement)."""
    return int(value) if value is not None else None


class WidgetToolProvider(ToolProvider):
    CREATE = "create_widget"
    UPDATE = "update_widget"

    def __init__(self, page_id: int, current_widgets: Optional[list[dict]] = None):
        self.page_id = page_id
        # Widgets keyed by id. Seeded with the page's *current* content (incl.
        # unsaved client drafts) so an update merges onto what the user actually
        # sees, then holds whatever the model creates/edits this run. Not
        # persisted — these are streamed to the browser for Save to commit.
        self._drafts: dict[str, WidgetDraft] = {
            c["id"]: WidgetDraft(
                id=c["id"],
                title=c.get("title") or "",
                data_query=c.get("data_query") or {},
                html=c.get("html") or "",
                css=c.get("css") or "",
                js=c.get("js") or "",
                grid_w=int(c.get("grid_w", 4)),
                grid_h=int(c.get("grid_h", 3)),
                state=c.get("state") or {},
            )
            for c in (current_widgets or [])
            if c.get("id")
        }

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
            id=page_repo.new_widget_id(),
            title=args.get("title") or "Untitled widget",
            data_query=args.get("data_query") or {},
            html=args.get("html", ""),
            css=args.get("css", ""),
            js=args.get("js", ""),
            grid_x=_opt_int(args.get("grid_x")),
            grid_y=_opt_int(args.get("grid_y")),
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
        widget = page_repo.get_widget(db.session, self.page_id, widget_id)
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