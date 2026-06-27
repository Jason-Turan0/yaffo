"""CRUD for the AI page builder's CustomPage / PageVersion / Widget / Conversation
tables.

Widgets and the conversation are **version-scoped**: a page owns a chain of
PageVersions, one of which is the live ``published_version`` (what presentation
renders) and at most one the in-flight ``working_version`` (a chat run generating
into it). See docs/development/ai-page-builder.md.

The page-scoped helpers here (get_widget, save_page_widgets, add_message,
set_widget_state, remove_widget) operate on the page's **published** version — the
manual-edit / presentation path, where "the page" means its live version. The
version primitives (fork_version, publish_version, delete_version,
set_version_status, add_version_message) drive the async-generation lifecycle.

Consumers go through this module so the page -> version -> widgets/messages joins
live in one place. Write helpers commit before returning (matching the other
repositories), so callers don't manage transactions.
"""
from __future__ import annotations

import uuid
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from yaffo.utils.time import utcnow
from yaffo.db.models import (
    PAGE_VERSION_STATUS_ACCEPTED,
    PAGE_VERSION_STATUS_IN_PROGRESS,
    Conversation,
    CustomPage,
    PageVersion,
    Widget,
)

# Client widget-entry keys that map onto Widget columns. Content keys share the
# model's own name; layout uses short x/y/w/h aliases.
_CONTENT_KEYS = ("title", "data_query", "state", "html", "css", "js")
_JSON_KEYS = ("data_query", "state")
_LAYOUT_KEYS = {"x": "grid_x", "y": "grid_y", "w": "grid_w", "h": "grid_h"}

# Widget columns copied verbatim when a fork snapshots a widget into a new version.
_COPY_FIELDS = (
    "title", "data_query", "state", "html", "css", "js",
    "grid_x", "grid_y", "grid_w", "grid_h",
)


def new_widget_id() -> str:
    """A fresh widget GUID. Widgets are identified by GUID so a draft's id is
    stable from creation through Save (the client can mint ids for manual adds)."""
    return uuid.uuid4().hex


def _with_relations(stmt):
    """Eager-load a page's published version with its widgets and messages so
    callers never repeat the join (page.widgets / page.messages read through it)."""
    return stmt.options(
        selectinload(CustomPage.published_version).selectinload(PageVersion.widgets),
        selectinload(CustomPage.published_version).selectinload(PageVersion.messages),
    )


def list_pages(session: Session) -> list[CustomPage]:
    """All pages in nav-tab order (tab_order ascending), with updated_at as the
    tiebreak for pages sharing a position."""
    stmt = _with_relations(
        select(CustomPage).order_by(CustomPage.tab_order.asc(), CustomPage.updated_at.desc())
    )
    return list(session.execute(stmt).scalars().all())


def get_page(session: Session, page_id: int) -> Optional[CustomPage]:
    """A page with its published version's widgets (ordered) and messages eager-
    loaded, or None."""
    stmt = _with_relations(select(CustomPage).where(CustomPage.id == page_id))
    return session.execute(stmt).scalars().first()


def get_version(session: Session, version_id: int) -> Optional[PageVersion]:
    """A single version with its widgets (ordered) and messages eager-loaded."""
    stmt = (
        select(PageVersion)
        .where(PageVersion.id == version_id)
        .options(selectinload(PageVersion.widgets), selectinload(PageVersion.messages))
    )
    return session.execute(stmt).scalars().first()


def get_widget(session: Session, page_id: int, widget_id: str) -> Optional[Widget]:
    """A single widget on the page's **published** version -- the join the frame /
    query / state routes would otherwise each repeat. None if the page, its
    published version, or the widget is missing."""
    stmt = (
        select(Widget)
        .join(CustomPage, CustomPage.published_version_id == Widget.version_id)
        .where(CustomPage.id == page_id, Widget.id == widget_id)
    )
    return session.execute(stmt).scalars().first()


def get_version_widget(session: Session, version_id: int, widget_id: str) -> Optional[Widget]:
    """A single widget within a specific version -- the agent's working version, which
    the widget tool reads/writes directly (distinct from get_widget, which resolves
    against a page's *published* version)."""
    stmt = select(Widget).where(Widget.version_id == version_id, Widget.id == widget_id)
    return session.execute(stmt).scalars().first()


def create_page(session: Session, title: str, subtitle: str = "") -> CustomPage:
    """Create a page with an initial empty ACCEPTED version as its published
    version -- every page always has one version that owns its (live) widgets. New
    pages land at the end of the nav strip (max tab_order + 1)."""
    next_order = (session.execute(select(func.max(CustomPage.tab_order))).scalar() or 0) + 1
    page = CustomPage(title=title, subtitle=subtitle, tab_order=next_order)
    session.add(page)
    session.flush()  # assign page.id for the version's FK
    version = PageVersion(
        page_id=page.id,
        status=PAGE_VERSION_STATUS_ACCEPTED,
        completed_at=utcnow(),
    )
    session.add(version)
    session.flush()  # assign version.id
    page.published_version_id = version.id
    session.commit()
    return page


def update_page(
    session: Session,
    page_id: int,
    title: Optional[str] = None,
    subtitle: Optional[str] = None,
    show_title: Optional[bool] = None,
    tab_order: Optional[int] = None,
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
    if tab_order is not None:
        _reposition(session, page, tab_order)
    page.updated_at = utcnow()
    session.commit()
    return page


def _reposition(session: Session, page: CustomPage, target: int) -> None:
    """Move `page` to nav position `target` (1-based), displacing the others, then
    re-sequence every page's tab_order to a unique, contiguous 1..N. Pulling the page
    out and re-inserting keeps the rest in their current relative order, so setting a
    page to 3 slots it third and shifts whoever held that spot down by one."""
    others = [p for p in _all_by_tab_order(session) if p.id != page.id]
    index = max(0, min(target - 1, len(others)))
    ordered = others[:index] + [page] + others[index:]
    for position, p in enumerate(ordered, start=1):
        p.tab_order = position


def _all_by_tab_order(session: Session) -> list[CustomPage]:
    stmt = select(CustomPage).order_by(CustomPage.tab_order.asc(), CustomPage.id.asc())
    return list(session.execute(stmt).scalars().all())


def delete_page(session: Session, page_id: int) -> None:
    page = get_page(session, page_id)
    if page is not None:
        session.delete(page)  # cascades versions -> widgets + messages
        session.commit()


def add_message(
    session: Session, page_id: int, entry_type: str, content: str
) -> Optional[Conversation]:
    """Append an entry to the page's published-version conversation (the live
    feed). `entry_type` is a CONVERSATION_TYPE_* (user/assistant for chat turns)."""
    page = get_page(session, page_id)
    if page is None or page.published_version is None:
        return None
    return _append_message(session, page.published_version_id, page, entry_type, content)


def add_version_message(
    session: Session, version_id: int, entry_type: str, content: str
) -> Optional[Conversation]:
    """Append an entry to a specific version's conversation -- the path the async
    task uses to record assistant/status/error lines onto the working version."""
    version = session.get(PageVersion, version_id)
    if version is None:
        return None
    return _append_message(session, version_id, version.page, entry_type, content)


def _append_message(
    session: Session, version_id: int, page: Optional[CustomPage], entry_type: str, content: str
) -> Conversation:
    message = Conversation(version_id=version_id, type=entry_type, content=content)
    session.add(message)
    if page is not None:
        page.updated_at = utcnow()
    session.commit()
    return message


def set_widget_state(session: Session, page_id: int, widget_id: str, state: dict) -> None:
    """Persist a widget's own state blob (re-injected as yaffo.state next render)."""
    widget = get_widget(session, page_id, widget_id)
    if widget is None:
        return
    widget.state = state or {}
    widget.version.page.updated_at = utcnow()
    session.commit()


def set_version_widget_state(session: Session, version_id: int, widget_id: str, state: dict) -> None:
    """Persist a widget's state on the version being displayed -- the broker targets
    a specific version's widget (unique per version), not whichever the published
    pointer happens to resolve to."""
    widget = get_version_widget(session, version_id, widget_id)
    if widget is None:
        return
    widget.state = state or {}
    if widget.version.page is not None:
        widget.version.page.updated_at = utcnow()
    session.commit()


def remove_widget(session: Session, page_id: int, widget_id: str) -> None:
    widget = get_widget(session, page_id, widget_id)
    if widget is None:
        return
    page = widget.version.page
    session.delete(widget)
    page.updated_at = utcnow()
    session.commit()


def remove_version_widget(session: Session, version_id: int, widget_id: str) -> None:
    """Delete a widget from a specific version -- the version the design page is
    editing (its working draft, or the published version when idle), targeted
    explicitly so there's no published-vs-working ambiguity."""
    widget = get_version_widget(session, version_id, widget_id)
    if widget is None:
        return
    page = widget.version.page
    session.delete(widget)
    if page is not None:
        page.updated_at = utcnow()
    session.commit()


def _apply_widget_fields(widget: Widget, item: dict) -> None:
    """Overlay a client widget entry onto a Widget row: content keys the client
    holds (drafts) win; omitted keys keep the stored value. Layout (x/y/w/h) always
    applies."""
    for key in _CONTENT_KEYS:
        if key in item:
            value = item[key]
            setattr(widget, key, value or {} if key in _JSON_KEYS else value)
    for payload_key, attr in _LAYOUT_KEYS.items():
        if payload_key in item:
            setattr(widget, attr, int(item[payload_key]))


def _write_widgets(session: Session, version: PageVersion, widgets: list[dict]) -> None:
    """Reconcile a version's widget set from a client list (add / edit / delete):
    the client is the source of truth -- entries carry layout always plus content
    for the drafts they hold, omitted content keeps the stored value, and any widget
    absent from the list is dropped."""
    existing = {w.id: w for w in version.widgets}
    seen: set[str] = set()
    for item in widgets:
        wid = str(item.get("id") or new_widget_id())
        widget = existing.get(wid)
        if widget is None:
            widget = Widget(id=wid, version_id=version.id)
            session.add(widget)
        _apply_widget_fields(widget, item)
        seen.add(wid)
    for wid, widget in existing.items():
        if wid not in seen:
            session.delete(widget)


def save_version_widgets(session: Session, version_id: int, widgets: list[dict]) -> None:
    """Commit the client's full widget set into a version (reconcile add/edit/delete).
    The client is the source of truth: each entry carries layout always plus content
    for the drafts it holds; omitted content keeps the stored value, and any widget
    absent from the list is dropped. Used to capture manual edits onto the working
    version before a follow-up generation or publish, and (via save_page_widgets) the
    manual-Save path onto the published version."""
    version = session.get(PageVersion, version_id)
    if version is None:
        return
    _write_widgets(session, version, widgets)
    if version.page is not None:
        version.page.updated_at = utcnow()
    session.commit()


def save_page_widgets(session: Session, page_id: int, widgets: list[dict]) -> None:
    """Commit the page's full widget set (the manual Save button) into its published
    version. Grid order is the layout coords, so there is no separate ordering to
    track."""
    page = get_page(session, page_id)
    if page is None or page.published_version_id is None:
        return
    save_version_widgets(session, page.published_version_id, widgets)


# Content fields the agent authors on a widget (state is widget-runtime and id is
# the key, so neither is agent-supplied) -- mirrors WidgetToolProvider's content set.
_AGENT_CONTENT_KEYS = ("title", "data_query", "html", "css", "js")
_AGENT_LAYOUT_KEYS = ("grid_x", "grid_y", "grid_w", "grid_h")


def _next_grid_bottom(version: PageVersion) -> int:
    """The y just below the current widgets -- where a new, unplaced widget lands."""
    return max((w.grid_y + w.grid_h for w in version.widgets), default=0)


def upsert_version_widget(
    session: Session, version_id: int, widget: dict
) -> Optional[Widget]:
    """Write an agent-produced widget (a WidgetDraft dict) into a version: create it
    -- placing a new widget with no explicit position at the bottom of the grid --
    or merge its changed content / layout onto the existing row. This is the async
    task's per-widget persistence, so a generation survives disconnect (the working
    version is the durable draft). grid_x/grid_y are None for "no explicit
    placement"; on create that means bottom, on update it means leave as-is."""
    version = session.get(PageVersion, version_id)
    if version is None:
        return None
    wid = str(widget.get("id") or new_widget_id())
    existing = next((w for w in version.widgets if w.id == wid), None)
    if existing is None:
        existing = Widget(
            id=wid,
            version_id=version_id,
            grid_x=int(widget.get("grid_x") or 0),
            grid_y=(int(widget["grid_y"]) if widget.get("grid_y") is not None
                    else _next_grid_bottom(version)),
            grid_w=int(widget.get("grid_w") or 4),
            grid_h=int(widget.get("grid_h") or 3),
        )
        session.add(existing)
    else:
        for attr in _AGENT_LAYOUT_KEYS:
            if widget.get(attr) is not None:
                setattr(existing, attr, int(widget[attr]))
    for key in _AGENT_CONTENT_KEYS:
        if widget.get(key) is not None:
            value = widget[key]
            setattr(existing, key, value or {} if key in _JSON_KEYS else value)
    version.page.updated_at = utcnow()
    session.commit()
    return existing


def _copy_widget(src: Widget, version_id: int) -> Widget:
    """A fresh Widget row carrying src's id + content, attached to a new version.
    JSON blobs are shallow-copied so the two rows don't share a dict."""
    values = {f: getattr(src, f) for f in _COPY_FIELDS}
    for key in _JSON_KEYS:
        values[key] = dict(values[key] or {})
    return Widget(id=src.id, version_id=version_id, **values)


def _seed_version_widgets(
    session: Session,
    version: PageVersion,
    published: Optional[PageVersion],
    widgets: Optional[list[dict]],
) -> None:
    """Seed a freshly forked version's widgets. With no client list, snapshot the
    published widgets verbatim. With a client list (the set the user currently
    sees), snapshot each published widget then overlay the client entry's layout /
    draft content; widgets absent from the list are dropped. This captures unsaved
    manual edits made before the chat request."""
    source = {w.id: w for w in (published.widgets if published else [])}
    if widgets is None:
        for src in source.values():
            session.add(_copy_widget(src, version.id))
        return
    for item in widgets:
        wid = str(item.get("id") or new_widget_id())
        src = source.get(wid)
        widget = _copy_widget(src, version.id) if src is not None else Widget(id=wid, version_id=version.id)
        session.add(widget)
        _apply_widget_fields(widget, item)


def fork_version(
    session: Session, page_id: int, widgets: Optional[list[dict]] = None
) -> Optional[PageVersion]:
    """Fork a new IN_PROGRESS working version from the page's published version: seed
    its widgets (from the client's current set, capturing unsaved edits -- see
    _seed_version_widgets) and copy the prior conversation so follow-ups have
    context. Sets working_version_id (locks the UI). Returns the new version."""
    page = get_page(session, page_id)
    if page is None:
        return None
    published = page.published_version
    version = PageVersion(
        page_id=page.id,
        status=PAGE_VERSION_STATUS_IN_PROGRESS,
        parent_version_id=published.id if published else None,
        started_at=utcnow(),
    )
    session.add(version)
    session.flush()  # assign version.id for the seeded widget / message rows
    _seed_version_widgets(session, version, published, widgets)
    if published is not None:
        for message in published.messages:
            session.add(Conversation(version_id=version.id, type=message.type, content=message.content))
    page.working_version_id = version.id
    page.updated_at = utcnow()
    session.commit()
    return version


def set_version_status(
    session: Session,
    version_id: int,
    status: str,
    *,
    error: Optional[str] = None,
    completed: bool = False,
) -> Optional[PageVersion]:
    """Transition a version's generation status (READY/FAILED/...), optionally
    recording an error and stamping completed_at."""
    version = session.get(PageVersion, version_id)
    if version is None:
        return None
    version.status = status
    if error is not None:
        version.error = error
    if completed:
        version.completed_at = utcnow()
    session.commit()
    return version


def restart_version(session: Session, version_id: int) -> Optional[PageVersion]:
    """Re-enter IN_PROGRESS on an existing working version for a follow-up generation
    (the user kept iterating on a READY/FAILED draft): reset timing and clear any
    prior error so the elapsed counter restarts and a stale failure doesn't linger."""
    version = session.get(PageVersion, version_id)
    if version is None:
        return None
    version.status = PAGE_VERSION_STATUS_IN_PROGRESS
    version.started_at = utcnow()
    version.completed_at = None
    version.error = None
    session.commit()
    return version


def publish_version(session: Session, version_id: int) -> Optional[PageVersion]:
    """Accept a working version as the live page: mark it ACCEPTED, point
    published_version_id at it, clear working_version_id (unlock), and drop the
    superseded published version (retention is a future option)."""
    version = session.get(PageVersion, version_id)
    if version is None:
        return None
    page = version.page
    old_published_id = page.published_version_id
    version.status = PAGE_VERSION_STATUS_ACCEPTED
    version.completed_at = version.completed_at or utcnow()
    page.published_version_id = version.id
    page.working_version_id = None
    page.updated_at = utcnow()
    session.flush()
    if old_published_id is not None and old_published_id != version.id:
        old = session.get(PageVersion, old_published_id)
        if old is not None:
            session.delete(old)
    session.commit()
    return version


def delete_version(session: Session, version_id: int) -> None:
    """Delete a version (cascading its widgets + conversation) and clear any page
    pointer to it. The cancel / failed-cleanup path; the UI snaps back to the
    published version."""
    version = session.get(PageVersion, version_id)
    if version is None:
        return
    page = version.page
    if page is not None:
        if page.working_version_id == version_id:
            page.working_version_id = None
        if page.published_version_id == version_id:
            page.published_version_id = None
        page.updated_at = utcnow()
    session.delete(version)
    session.commit()