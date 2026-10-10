"""Test: what a sync run would do, without doing it.

Targets: the diff against the ledger ("would add 12, remove 3") and how many photos
would be converted. Sources: the first page of records, each marked new, already
imported, already in the library, or edited. No files are written, nothing is
downloaded, and the ledger isn't touched.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy.orm import Session

from yaffo.db.models import (
    Automation, MediaItem,
    SYNC_ITEM_CANT_REMOVE, SYNC_ITEM_DELETED_AT_SOURCE, SYNC_ITEM_FAILED, SYNC_ITEM_PENDING,
    SYNC_ITEM_REMOVED_REMOTELY, SYNC_ITEM_REMOVING, SYNC_ITEM_SENT, SYNC_ROLE_SOURCE,
)
from yaffo.db.repositories import sync_repository
from yaffo.sync import access, setup
from yaffo.sync.adapters import AdapterUnavailable, LoadedAdapter, load_adapter, source_handler, target_handler
from yaffo.sync.convert import UnsupportedFormat, needs_conversion
from yaffo.sync.files import file_sha1
from yaffo.sync.handlers import ERROR_AUTH, ERROR_NOT_FOUND, HookContext
from yaffo.sync.schemas import (
    PASS_INCREMENTAL, PAUSE_OPTION_MISSING, PAUSE_SELECTION_MISSING, SyncColumn, SyncSchemaError,
)
from yaffo.sync.selection import SelectionMissing, resolve_selection
from yaffo.sync.starlark_adapter import HookFailed
from yaffo.sync.target_run import PERMANENT_ITEM_ERRORS, item_key

SAMPLE_SIZE = 8
CONVERSION_CHECK_LIMIT = 500
HASH_CHECK_LIMIT = 50

RECORD_NEW = "new"
RECORD_IMPORTED = "imported"
RECORD_IN_LIBRARY = "in_library"
RECORD_CHANGED = "changed"
RECORD_DELETED = "deleted_at_source"

PROBLEM_SERVICE_ERROR = "service_error"
PROBLEM_SIGN_IN = "needs_sign_in"


@dataclass(frozen=True)
class RecordPreview:
    filename: str
    size: int | None
    state: str


@dataclass
class SyncTestResult:
    role: str
    problem: str | None = None
    would_add: int = 0
    would_remove: int = 0
    unchanged: int = 0
    convert: int = 0
    unsupported: int = 0
    add_samples: list[str] = field(default_factory=list)
    remove_samples: list[str] = field(default_factory=list)
    records: list[RecordPreview] = field(default_factory=list)
    more: bool = False
    detail: str | None = None


def _target(session: Session, automation: Automation, column: SyncColumn, adapter: LoadedAdapter) -> SyncTestResult:
    handler = target_handler(adapter)
    options = column.config.options
    result = SyncTestResult(role=column.config.role)
    result.problem = handler.destination_problem(options)
    caps = handler.capabilities(options)
    try:
        desired = resolve_selection(session, column.config.selection, automation.name,
                                    automation.published_code)
    except SelectionMissing:
        result.problem = PAUSE_SELECTION_MISSING
        return result
    except ValueError as e:
        result.problem, result.detail = PAUSE_SELECTION_MISSING, str(e)
        return result
    rows = {row.item_key: row for row in sync_repository.rows(session, automation.id)}
    wanted = {item_key(d.media_item_id, d.target, caps): d for d in desired}
    to_add = []
    for key, d in wanted.items():
        row = rows.get(key)
        if row is None or row.status not in (SYNC_ITEM_SENT, SYNC_ITEM_REMOVED_REMOTELY, SYNC_ITEM_CANT_REMOVE,
                                             SYNC_ITEM_REMOVING):
            if row is not None and row.status == SYNC_ITEM_FAILED and row.error_code in PERMANENT_ITEM_ERRORS:
                continue
            to_add.append(d)
        elif row.status == SYNC_ITEM_SENT:
            result.unchanged += 1
    to_remove = [row for row in rows.values() if row.status == SYNC_ITEM_REMOVING
                 or (row.status in (SYNC_ITEM_SENT, SYNC_ITEM_PENDING) and row.item_key not in wanted
                     and row.remote_id is not None)]
    result.would_add, result.would_remove = len(to_add), len(to_remove)
    media = {m.id: m for m in session.query(MediaItem).filter(
        MediaItem.id.in_([d.media_item_id for d in to_add[:CONVERSION_CHECK_LIMIT]])).all()}
    for d in to_add[:CONVERSION_CHECK_LIMIT]:
        item = media.get(d.media_item_id)
        if item is None:
            continue
        try:
            if Path(item.full_file_path).is_file() and needs_conversion(Path(item.full_file_path), caps.accepts):
                result.convert += 1
        except UnsupportedFormat:
            result.unsupported += 1
    result.add_samples = [Path(media[d.media_item_id].full_file_path).name
                          for d in to_add[:SAMPLE_SIZE] if d.media_item_id in media]
    result.remove_samples = [row.remote_id or row.item_key for row in to_remove[:SAMPLE_SIZE]]
    return result


def _source(session: Session, automation: Automation, column: SyncColumn, adapter: LoadedAdapter) -> SyncTestResult:
    """The first page, live. A web source reads through the sync's account with read
    grants only; nothing is downloaded."""
    handler = source_handler(adapter)
    options = column.config.options
    result = SyncTestResult(role=column.config.role)
    result.problem = handler.source_problem(options)
    if result.problem:
        return result
    cursor = column.runtime.cursor("list")
    client = access.attach(session, adapter, column.config, read_only=True, vouched=column.runtime.followups)
    ctx = HookContext(options=options, state={**column.runtime.state, "checkpoint": cursor.checkpoint},
                      run_id=None, pass_kind=PASS_INCREMENTAL, preview=True, automation_id=automation.id)
    try:
        page = handler.list_page(None, ctx)
    except HookFailed as e:
        result.problem, result.detail = PROBLEM_SERVICE_ERROR, str(e)
        return result
    error = page.get("error")
    if (client is not None and client.auth_failed) or (error and error.get("code") == ERROR_AUTH):
        result.problem = PROBLEM_SIGN_IN
        return result
    if error:
        result.problem = PAUSE_OPTION_MISSING if error.get("code") == ERROR_NOT_FOUND else PROBLEM_SERVICE_ERROR
        result.detail = error.get("message") or error.get("code")
        return result
    records = [r for r in page.get("records") or [] if isinstance(r, dict) and r.get("source_id")]
    known = sync_repository.rows_by_key(session, automation.id, [r["source_id"] for r in records])
    for index, record in enumerate(records):
        row = known.get(record["source_id"])
        if record.get("deleted"):
            state = RECORD_DELETED
        elif row is not None and row.status != SYNC_ITEM_FAILED:
            state = (RECORD_IMPORTED if row.fingerprint == record.get("updated_at")
                     or row.status == SYNC_ITEM_DELETED_AT_SOURCE else RECORD_CHANGED)
        else:
            state = RECORD_NEW
            digest = record.get("hash") or {}
            sha1 = digest.get("value") if digest.get("algo") == "sha1" else None
            if sha1 is None and index < HASH_CHECK_LIMIT and record.get("local_path"):
                try:
                    sha1 = file_sha1(Path(record["local_path"]))
                except OSError:
                    sha1 = None
            if sha1 and session.query(MediaItem.id).filter(MediaItem.sha1 == sha1).first():
                state = RECORD_IN_LIBRARY
        result.records.append(RecordPreview(filename=record.get("filename") or record["source_id"],
                                            size=None if record.get("deleted") else record.get("size"),
                                            state=state))
    result.would_add = sum(1 for r in result.records if r.state == RECORD_NEW)
    result.more = page.get("next_cursor") is not None
    return result


def preview_sync(session: Session, automation: Automation) -> SyncTestResult:
    try:
        column = sync_repository.load_sync(automation)
        adapter = load_adapter(session, column.config.adapter_id)
    except (SyncSchemaError, AdapterUnavailable):
        return SyncTestResult(role="", problem="invalid_config")
    missing = setup.missing_setup(session, automation)
    if "account" in missing:
        return SyncTestResult(role=column.config.role, problem=PROBLEM_SIGN_IN)
    if any(code.startswith("option:") for code in missing):
        return SyncTestResult(role=column.config.role, problem="setup_incomplete")
    if column.config.role == SYNC_ROLE_SOURCE:
        return _source(session, automation, column, adapter)
    if column.config.selection is None:
        return SyncTestResult(role=column.config.role, problem="setup_incomplete")
    return _target(session, automation, column, adapter)
