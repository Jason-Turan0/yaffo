"""ORM -> browser serializers for the page builder.

Turns persisted ``Widget`` / ``PageVersion`` rows into the shapes the browser
parses. Kept distinct from ``schemas.py`` (pure wire shapes, no ORM) and from the
SQLAlchemy models: this module is the one place an entity becomes a wire payload.

- ``widget_draft`` reuses ``WidgetDraft`` (the browser widget shape) so a persisted
  version widget renders through the existing preview path.
- ``version_status_payload`` is the poll contract for an in-flight generation: the
  status the client gates Save/Cancel on, the live conversation feed, and the
  version's widgets to render.
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from typing import Optional

from yaffo.db.models import PageVersion, Widget
from yaffo.site_agents.schemas import WidgetDraft


def _utc_iso(value: Optional[datetime]) -> Optional[str]:
    """Naive timestamps are stored in UTC (datetime.utcnow); stamp them as UTC so the
    browser's Date parser doesn't read them as local time (which breaks the elapsed
    counter)."""
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc).isoformat()


def widget_draft(widget: Widget) -> dict:
    """A persisted version widget as a WidgetDraft dict (the browser widget shape)."""
    return asdict(
        WidgetDraft(
            id=widget.id,
            title=widget.title,
            data_query=widget.data_query or {},
            html=widget.html or "",
            css=widget.css or "",
            js=widget.js or "",
            grid_x=widget.grid_x,
            grid_y=widget.grid_y,
            grid_w=widget.grid_w,
            grid_h=widget.grid_h,
            state=widget.state or {},
        )
    )


def version_status_payload(version: PageVersion) -> dict:
    """The poll response for an in-flight generation: status + timing, the
    conversation feed (user / assistant / status / error lines), and the version's
    widgets. The client polls this until a terminal status, gating Save on READY."""
    return {
        "version_id": version.id,
        "status": version.status,
        "started_at": _utc_iso(version.started_at),
        "completed_at": _utc_iso(version.completed_at),
        "error": version.error,
        "messages": [{"type": m.type, "content": m.content} for m in version.messages],
        "widgets": [widget_draft(w) for w in version.widgets],
    }