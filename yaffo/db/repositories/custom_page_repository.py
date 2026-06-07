"""CRUD for the AI page builder's CustomPage / Widget / Conversation tables.

Consumers go through this module so the page -> widgets/messages joins live in one
place: get_page eager-loads both relationships, and get_widget resolves a widget
within its page in a single scoped query. Write helpers commit before returning
(matching the other repositories), so callers don't manage transactions.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from yaffo.db.models import Conversation, CustomPage, Widget

# Client widget-entry keys that map onto Widget columns. Content keys share the
# model's own name; layout uses short x/y/w/h aliases.
_CONTENT_KEYS = ("title", "data_query", "state", "html", "css", "js")
_JSON_KEYS = ("data_query", "state")
_LAYOUT_KEYS = {"x": "grid_x", "y": "grid_y", "w": "grid_w", "h": "grid_h"}


def new_widget_id() -> str:
    """A fresh widget GUID. Widgets are identified by GUID so a draft's id is
    stable from creation through Save (the client can mint ids for manual adds)."""
    return uuid.uuid4().hex


def _with_relations(stmt):
    """Eager-load a page's widgets and messages so callers never repeat the join."""
    return stmt.options(
        selectinload(CustomPage.widgets),
        selectinload(CustomPage.messages),
    )


def list_pages(session: Session) -> list[CustomPage]:
    """All pages, most-recently-updated first (the order used for the nav tabs)."""
    stmt = _with_relations(select(CustomPage).order_by(CustomPage.updated_at.desc()))
    return list(session.execute(stmt).scalars().all())


def get_page(session: Session, page_id: int) -> Optional[CustomPage]:
    """A page with its widgets (ordered) and messages eager-loaded, or None."""
    stmt = _with_relations(select(CustomPage).where(CustomPage.id == page_id))
    return session.execute(stmt).scalars().first()


def get_widget(session: Session, page_id: int, widget_id: str) -> Optional[Widget]:
    """A single widget scoped to its page -- the join the frame / query / state
    routes would otherwise each repeat. None if either page or widget is missing."""
    stmt = select(Widget).where(Widget.page_id == page_id, Widget.id == widget_id)
    return session.execute(stmt).scalars().first()


def create_page(session: Session, title: str, subtitle: str = "") -> CustomPage:
    page = CustomPage(title=title, subtitle=subtitle)
    session.add(page)
    session.commit()
    return page


def update_page(
    session: Session,
    page_id: int,
    title: Optional[str] = None,
    subtitle: Optional[str] = None,
    show_title: Optional[bool] = None,
) -> Optional[CustomPage]:
    page = get_page(session, page_id)
    if page is None:
        return None
    if title is not None:
        page.title = title
    if subtitle is not None:
        page.subtitle = subtitle
    if show_title is not None:
        page.show_title = show_title
    page.updated_at = datetime.utcnow()
    session.commit()
    return page


def delete_page(session: Session, page_id: int) -> None:
    page = get_page(session, page_id)
    if page is not None:
        session.delete(page)
        session.commit()


def add_message(session: Session, page_id: int, role: str, content: str) -> Optional[Conversation]:
    page = get_page(session, page_id)
    if page is None:
        return None
    message = Conversation(page_id=page_id, role=role, content=content)
    session.add(message)
    page.updated_at = datetime.utcnow()
    session.commit()
    return message


def set_widget_state(session: Session, page_id: int, widget_id: str, state: dict) -> None:
    """Persist a widget's own state blob (re-injected as yaffo.state next render)."""
    widget = get_widget(session, page_id, widget_id)
    if widget is None:
        return
    widget.state = state or {}
    widget.page.updated_at = datetime.utcnow()
    session.commit()


def remove_widget(session: Session, page_id: int, widget_id: str) -> None:
    widget = get_widget(session, page_id, widget_id)
    if widget is None:
        return
    page = widget.page
    session.delete(widget)
    page.updated_at = datetime.utcnow()
    session.commit()


def _apply_widget_fields(widget: Widget, item: dict) -> None:
    """Overlay a client widget entry onto a Widget row: content keys the client
    holds (drafts) win; omitted keys keep the stored value. Layout (x/y/w/h) always
    applies, and Save marks the widget ready."""
    for key in _CONTENT_KEYS:
        if key in item:
            value = item[key]
            setattr(widget, key, value or {} if key in _JSON_KEYS else value)
    for payload_key, attr in _LAYOUT_KEYS.items():
        if payload_key in item:
            setattr(widget, attr, int(item[payload_key]))


def save_page_widgets(session: Session, page_id: int, widgets: list[dict]) -> None:
    """Commit the page's full widget set from the client (the Save button) -- the
    only thing that writes widget content. The client is the source of truth: each
    entry carries layout always, plus content for the drafts it holds; entries that
    omit content keep the stored value, and any widget absent from the list is
    dropped. Reconciles add / edit / delete in one shot (grid order is the layout
    coords, so there is no separate ordering to track)."""
    page = get_page(session, page_id)
    if page is None:
        return
    existing = {w.id: w for w in page.widgets}
    seen: set[str] = set()
    for item in widgets:
        wid = str(item.get("id") or new_widget_id())
        widget = existing.get(wid)
        if widget is None:
            widget = Widget(id=wid, page_id=page.id)
            session.add(widget)
        _apply_widget_fields(widget, item)
        seen.add(wid)
    for wid, widget in existing.items():
        if wid not in seen:
            session.delete(widget)
    page.updated_at = datetime.utcnow()
    session.commit()