"""Merge the page's stored widgets with the client's current grid state.

The chat route sends the widgets the browser currently holds (saved widgets plus
unsaved drafts, each with live layout). To edit with full sight of the real
html/css/js, the agent needs each widget's *current* content: stored content
overlaid with any client-provided fields (unsaved drafts win), in client order.

Read-only — nothing is persisted. Produces plain dicts (the shape the prompt
builder and the widget tool consume), so it lives here rather than on the route.
"""
from __future__ import annotations

from typing import Any

# Defaults for a client widget with no stored counterpart yet (a brand-new draft
# the client holds). The client normally supplies the content; these only fill
# fields it omits.
_NEW_WIDGET_DEFAULTS: dict[str, Any] = {
    "title": "Untitled widget", "data_query": {}, "state": {},
    "html": "", "css": "", "js": "",
    "grid_x": 0, "grid_y": 0, "grid_w": 4, "grid_h": 3,
}


def _base(stored, field: str) -> Any:
    """The stored widget's value for a field, or the new-draft default."""
    return getattr(stored, field) if stored is not None else _NEW_WIDGET_DEFAULTS[field]


def merge_widget_content(stored_widgets: list, client_widgets: list[dict]) -> list[dict]:
    """Current content of each widget on the client's grid: stored content overlaid
    with client-provided fields (drafts win), in client order. `stored_widgets` is
    storage-agnostic (any object exposing the widget fields). Layout uses the
    client's x/y/w/h keys; everything else shares the model's own name."""
    existing = {w.id: w for w in stored_widgets}
    resolved: list[dict] = []
    for item in client_widgets:
        wid = str(item.get("id") or "")
        stored = existing.get(wid)
        resolved.append({
            "id": wid,
            "title": item.get("title", _base(stored, "title")),
            "data_query": item.get("data_query", _base(stored, "data_query")) or {},
            "state": item.get("state", _base(stored, "state")) or {},
            "html": item.get("html", _base(stored, "html")),
            "css": item.get("css", _base(stored, "css")),
            "js": item.get("js", _base(stored, "js")),
            "grid_x": int(item.get("x", _base(stored, "grid_x"))),
            "grid_y": int(item.get("y", _base(stored, "grid_y"))),
            "grid_w": int(item.get("w", _base(stored, "grid_w"))),
            "grid_h": int(item.get("h", _base(stored, "grid_h"))),
        })
    return resolved