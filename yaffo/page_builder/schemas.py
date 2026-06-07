"""Wire/DTO contracts for the page builder — the shapes the feature hands to the
browser, kept separate from the SQLAlchemy entities (yaffo/db/models.py) and from
the internal layer types (agent events, model-client and tool-provider interfaces,
which stay with their layers).

Two audiences are served here:

- **WidgetDraft** — a generated/edited widget streamed to the browser and held as
  an unsaved draft until Save. A deliberately narrower shape than the persisted
  ``Widget`` (no page_id / position / prompt / status), and with ``grid_x``/
  ``grid_y`` optional so "no explicit placement" round-trips as null.
- **chat_* records** — the discriminated set of newline-delimited JSON records the
  chat stream emits (`POST /pages/<id>/chat`). Materialised as constructors so the
  event vocabulary lives in one place instead of inline dict literals in the route.

Routes import these and serialize; they do not define payload shapes inline.
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


# --- chat stream records (application/x-ndjson) ----------------------------
# Each constructor returns one record dict; the route wraps it as one NDJSON line.
# The client switches on `event` (see static/pages/grid.js): message -> chat bubble,
# status -> the pinned status row, widget_new/widget_updated -> draft on the grid,
# done -> end of run.

def chat_message(content: str) -> dict:
    """An assistant turn (also persisted)."""
    return {"event": "message", "content": content}


def chat_status(text: str) -> dict:
    """Progress text for the pinned status row while a tool runs."""
    return {"event": "status", "text": text}


def chat_widget_new(widget: dict) -> dict:
    """A newly drafted widget's content (a WidgetDraft as a dict)."""
    return {"event": "widget_new", "widget": widget}


def chat_widget_updated(widget: dict) -> dict:
    """An edited widget's content, to re-render in place."""
    return {"event": "widget_updated", "widget": widget}


def chat_done() -> dict:
    """Terminal record: the run finished."""
    return {"event": "done"}