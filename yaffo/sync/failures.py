"""What went wrong in the real runs of syncs using one adapter, for the service builder.

Test never sends a write, so a target's refusals (a service rejecting an upload's
fields, say) only show up when a sync runs for real. This reads those runs back:
each sync's last run, its item counts, and its most recent failed items with the
service's own message (`details.error_message`, kept by the engine).

It describes each item by what explains a failure (status, code, message,
attempts, file type, size, and the capture date exactly as Yaffo passed it) and
never by filename or path.
"""
from __future__ import annotations

import json
import mimetypes
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from yaffo.db.models import Automation, Job, MediaItem, SyncItem, SYNC_ROLE_TARGET
from yaffo.db.repositories import sync_repository
from yaffo.sync.run_common import ERROR_MESSAGE_KEY

MAX_ITEMS_PER_SYNC = 10
MODEL_TEXT_CHARS = 6000


@dataclass
class SyncFailures:
    name: str
    role: str
    last_run: dict[str, Any] | None
    counts: dict[str, int]
    items: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class FailureReport:
    syncs: list[SyncFailures]

    def model_text(self) -> str:
        if not self.syncs:
            return ("No sync uses this service yet, so there are no real runs to read. Once the user publishes "
                    "it and sets up a sync, its runs' failures show up here.")
        lines = []
        for sync in self.syncs:
            lines.append(f"Sync {sync.name!r} ({sync.role}). Last run: {_compact(sync.last_run) if sync.last_run else 'never'}. "
                         f"Items by status: {_compact(sync.counts)}.")
            if not sync.items:
                lines.append("  No failed items.")
            for item in sync.items:
                lines.append(f"  - {_compact(item)}")
        return "\n".join(lines)[:MODEL_TEXT_CHARS]

    def to_dict(self) -> dict[str, Any]:
        return {"syncs": [{"name": s.name, "role": s.role, "last_run": s.last_run, "counts": s.counts,
                           "items": s.items} for s in self.syncs]}


def failure_report(session: Session, adapter_id: int) -> FailureReport:
    return FailureReport([_sync_failures(session, automation)
                          for automation in sync_repository.syncs_using_adapter(session, adapter_id)])


def _sync_failures(session: Session, automation: Automation) -> SyncFailures:
    role = sync_repository.load_sync(automation).config.role
    counts = dict(session.query(SyncItem.status, func.count(SyncItem.id))
                  .filter(SyncItem.automation_id == automation.id).group_by(SyncItem.status).all())
    failed = (session.query(SyncItem)
              .filter(SyncItem.automation_id == automation.id, SyncItem.error_code.isnot(None))
              .order_by(SyncItem.updated_at.desc(), SyncItem.id.desc()).limit(MAX_ITEMS_PER_SYNC).all())
    return SyncFailures(name=automation.name, role=role, last_run=_last_run(session, automation.id),
                        counts=counts, items=[_describe(session, row, role) for row in failed])


def _last_run(session: Session, automation_id: int) -> dict[str, Any] | None:
    job = (session.query(Job).filter(Job.automation_id == automation_id)
           .order_by(Job.created_at.desc()).first())
    if job is None:
        return None
    run: dict[str, Any] = {"status": job.status, "at": str(job.completed_at or job.started_at or job.created_at)}
    try:
        outcome = json.loads(job.job_data) if job.job_data else None
    except ValueError:
        outcome = None
    if isinstance(outcome, dict):
        run["outcome"] = outcome
    if job.error:
        run["error"] = job.error[:300]
    return run


def _describe(session: Session, row: SyncItem, role: str) -> dict[str, Any]:
    details = row.details or {}
    item: dict[str, Any] = {"status": row.status, "code": row.error_code, "attempts": row.attempts}
    if details.get(ERROR_MESSAGE_KEY):
        item["message"] = details[ERROR_MESSAGE_KEY]
    if role == SYNC_ROLE_TARGET:
        media = session.get(MediaItem, row.media_item_id) if row.media_item_id else None
        if media is not None:
            path = Path(media.full_file_path)
            item["type"] = mimetypes.guess_type(path.name)[0] or path.suffix.lower() or None
            item["size"] = path.stat().st_size if path.is_file() else None
            # As the push batch item carries it (target_run.prepare).
            item["taken_at"] = {"local": media.date_taken} if media.date_taken else {}
        if details.get("sent_as"):
            item["sent_as"] = details["sent_as"]
    else:
        record = details.get("record") or {}
        for key, name in (("mime", "type"), ("size", "size"), ("taken_at", "taken_at")):
            if key in record:
                item[name] = record[key]
    return item


def _compact(value: Any) -> str:
    return json.dumps(value, default=str, separators=(", ", ": "))
