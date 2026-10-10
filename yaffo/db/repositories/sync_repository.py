"""Persistence for sync automations: the `automations.sync` JSON and the
`sync_items` ledger. See docs/development/automation-sync-adapters.md (Data model)."""
from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import func, or_
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from yaffo.db.models import (
    Automation, MediaItem, SyncItem,
    SYNC_ITEM_FAILED, SYNC_ITEM_PENDING, SYNC_ITEM_REMOVING, SYNC_ITEM_SENT,
)
from yaffo.sync.schemas import SyncColumn, SyncConfig, SyncRuntime

_CHUNK = 500


def load_sync(automation: Automation) -> SyncColumn:
    """The automation's sync JSON, validated. Raises SyncSchemaError."""
    return SyncColumn.from_dict(automation.sync)


def save_config(automation: Automation, config: SyncConfig) -> None:
    """Replace the config part (UI and consent only). The caller commits."""
    current = dict(automation.sync or {})
    current["config"] = config.to_dict()
    current.setdefault("runtime", SyncRuntime().to_dict())
    automation.sync = current
    flag_modified(automation, "sync")


def save_runtime(automation: Automation, runtime: SyncRuntime) -> None:
    """Replace the runtime part (the engine only). The caller commits, together with
    the ledger rows the runtime describes."""
    current = dict(automation.sync or {})
    current["runtime"] = runtime.to_dict()
    automation.sync = current
    flag_modified(automation, "sync")


def sync_automations(session: Session) -> list[Automation]:
    return session.query(Automation).filter(Automation.sync.isnot(None)).order_by(Automation.name).all()


def rows(session: Session, automation_id: int, statuses: Iterable[str] | None = None) -> list[SyncItem]:
    query = session.query(SyncItem).filter(SyncItem.automation_id == automation_id)
    if statuses is not None:
        query = query.filter(SyncItem.status.in_(list(statuses)))
    return query.order_by(SyncItem.id).all()


def rows_by_key(session: Session, automation_id: int, item_keys: list[str]) -> dict[str, SyncItem]:
    found: dict[str, SyncItem] = {}
    for start in range(0, len(item_keys), _CHUNK):
        chunk = item_keys[start:start + _CHUNK]
        for row in (session.query(SyncItem)
                    .filter(SyncItem.automation_id == automation_id, SyncItem.item_key.in_(chunk)).all()):
            found[row.item_key] = row
    return found


def _basename(path):
    """A path's last segment in SQL (SQLite has no basename): trim the trailing
    non-separator characters to get the folder, then cut it off."""
    normal = func.replace(path, "\\", "/")
    return func.replace(normal, func.rtrim(normal, func.replace(normal, "/", "")), "")


def _escape_like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _ledger_query(session: Session, automation_id: int, search: str | None):
    """The sync's rows, narrowed to those whose file name contains `search`: the
    library file's name, or (for rows with no library photo: failed imports,
    deletions at the source) the name the source gave, or the item's key or
    remote id (a Folder target's path)."""
    query = (session.query(SyncItem)
             .outerjoin(MediaItem, MediaItem.id == SyncItem.media_item_id)
             .filter(SyncItem.automation_id == automation_id))
    search = (search or "").strip()
    if search:
        pattern = f"%{_escape_like(search)}%"
        query = query.filter(or_(
            _basename(MediaItem.full_file_path).ilike(pattern, escape="\\"),
            func.json_extract(SyncItem.details, "$.filename").ilike(pattern, escape="\\"),
            SyncItem.remote_id.ilike(pattern, escape="\\"),
            SyncItem.item_key.ilike(pattern, escape="\\"),
        ))
    return query


def status_counts(session: Session, automation_id: int, search: str | None = None) -> dict[str, int]:
    query = _ledger_query(session, automation_id, search)
    return dict(query.with_entities(SyncItem.status, func.count(SyncItem.id)).group_by(SyncItem.status).all())


def items_page(session: Session, automation_id: int, status: str | None, offset: int, limit: int,
               search: str | None = None) -> list[tuple[SyncItem, MediaItem | None]]:
    """One page of the ledger, newest first, with each row's library photo."""
    query = _ledger_query(session, automation_id, search)
    if status:
        query = query.filter(SyncItem.status == status)
    return (query.add_entity(MediaItem)
            .order_by(SyncItem.updated_at.desc(), SyncItem.id.desc())
            .offset(offset).limit(limit).all())


def mark_removing_for_deleted_media(session: Session, media_item_ids: list[int]) -> list[int]:
    """Queue the remote removal of photos about to be deleted from the library, in
    the delete's own transaction (docs: *Photos deleted from Yaffo*).

    Target rows that were sent or are pending become `removing` (they keep remote_id
    and target, which is all removal needs); rows that never reached the target
    (`failed`) are dropped. Then every ledger row's media_item_id is cleared, which the
    foreign key would do if SQLite enforced it. Returns the ids of the target syncs
    that now have removals queued. Doesn't commit."""
    if not media_item_ids:
        return []
    affected: set[int] = set()
    for start in range(0, len(media_item_ids), _CHUNK):
        chunk = media_item_ids[start:start + _CHUNK]
        queued = (session.query(SyncItem.automation_id)
                  .filter(SyncItem.media_item_id.in_(chunk),
                          SyncItem.status.in_([SYNC_ITEM_SENT, SYNC_ITEM_PENDING, SYNC_ITEM_REMOVING]))
                  .distinct().all())
        affected.update(row[0] for row in queued)
        (session.query(SyncItem)
         .filter(SyncItem.media_item_id.in_(chunk), SyncItem.status.in_([SYNC_ITEM_SENT, SYNC_ITEM_PENDING]))
         .update({SyncItem.status: SYNC_ITEM_REMOVING}, synchronize_session=False))
        (session.query(SyncItem)
         .filter(SyncItem.media_item_id.in_(chunk), SyncItem.status == SYNC_ITEM_FAILED,
                 SyncItem.remote_id.is_(None))
         .delete(synchronize_session=False))
        (session.query(SyncItem)
         .filter(SyncItem.media_item_id.in_(chunk))
         .update({SyncItem.media_item_id: None}, synchronize_session=False))
    return sorted(affected)
