"""Widget tools: create_widget and update_widget.

Tools the agent calls to create or edit widgets on a page. The provider is scoped
to a single working **version** at construction, so the model can only touch that
version — it supplies content; the server owns placement and persistence.

These tools **persist directly to the version in the database** (via
``upsert_version_widget``): the working version is the durable draft, so a
generation's widgets survive disconnect and the browser observes them by polling.
The published page is untouched until the version is accepted (published).
"""
from __future__ import annotations

import dataclasses
from typing import Optional, get_args, get_type_hints

from sqlalchemy.orm import Session

from yaffo.db.repositories import custom_page_repository as page_repo
from yaffo.db.repositories.data_query_repository import DATA_QUERY_SCHEMA
from yaffo.site_agents import serializers
from yaffo.site_agents.schemas import WidgetDraft
from yaffo.site_agents.tool_providers.tool_provider_types import (
    CallToolReturn,
    RawToolDefinition,
    ToolProvider,
    ToolResult,
)


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

    def __init__(self, version_id: int, session: Session):
        # The working version this run writes into, and the session used to persist
        # (a request's db.session or a worker's SessionFactory session — the tool
        # doesn't care which).
        self.version_id = version_id
        self.session = session

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
        # Persist a new widget into the version. grid_x/grid_y omitted -> the repo
        # places it at the bottom of the current grid (computed from the persisted
        # set, so sequential creates stack rather than collide).
        content = {
            "id": page_repo.new_widget_id(),
            "title": args.get("title") or "Untitled widget",
            "data_query": args.get("data_query") or {},
            "html": args.get("html", ""),
            "css": args.get("css", ""),
            "js": args.get("js", ""),
            "grid_x": _opt_int(args.get("grid_x")),
            "grid_y": _opt_int(args.get("grid_y")),
            "grid_w": int(args.get("grid_w", 4)),
            "grid_h": int(args.get("grid_h", 3)),
        }
        widget = page_repo.upsert_version_widget(self.session, self.version_id, content)
        return ToolResult(
            model_text=f'Created widget {widget.id} "{widget.title}".',
            host_data=serializers.widget_draft(widget),
        )

    def _update(self, args: dict) -> CallToolReturn:
        widget_id = args.get("widget_id")
        if not widget_id:
            return "update_widget requires widget_id."
        if page_repo.get_version_widget(self.session, self.version_id, widget_id) is None:
            return f"Widget {widget_id} not found on this page version."
        # Only the supplied fields change; omitted ones (incl. position) are left
        # as-is by the repo merge.
        changed = [k for k in _CONTENT_FIELDS if args.get(k) is not None]
        content = {"id": widget_id, **{k: args[k] for k in changed}}
        widget = page_repo.upsert_version_widget(self.session, self.version_id, content)
        return ToolResult(
            model_text=f'Updated widget {widget.id} "{widget.title}" (changed: {", ".join(changed) or "nothing"}).',
            host_data=serializers.widget_draft(widget),
        )