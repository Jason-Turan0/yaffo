"""Wire/DTO contracts for the page builder — the shapes the feature hands to the
browser, kept separate from the SQLAlchemy entities (yaffo/db/models.py) and from
the internal layer types (agent events, model-client and tool-provider interfaces,
which stay with their layers).

- **WidgetDraft** — the browser-facing widget shape: a generated/edited widget's
  content + suggested size. A deliberately narrower shape than the persisted
  ``Widget`` (no version_id), with ``grid_x``/``grid_y`` optional so "no explicit
  placement" round-trips as null. Reused by ``serializers.widget_draft`` (the poll
  payload) and to derive the widget tool's model-facing schema.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class WidgetDraft:
    """The widget schema sent to the browser (distinct from the tool_result text
    sent to the model). Mirrors the persisted widget's content + suggested size.

    ``grid_x``/``grid_y`` are optional: None means "no explicit placement" — a new
    widget lands at the bottom, an edited one stays where it is."""
    id: str
    title: str
    data_query: dict = field(default_factory=dict)
    html: str = ""
    css: str = ""
    js: str = ""
    grid_x: Optional[int] = None
    grid_y: Optional[int] = None
    grid_w: int = 4
    grid_h: int = 3
    state: dict = field(default_factory=dict)