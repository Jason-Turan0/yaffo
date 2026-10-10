"""One run of a source sync: bring new files from the source into the import root.

    loop pages from the stored cursor (resumes mid-pass):
        record seen with the same updated_at          -> skip
        known record, new updated_at, same content    -> just note the new updated_at
        known record, new content                     -> replace the library's copy (old one to the trash)
        content already in the library (sha1)         -> link, don't copy
        otherwise                                     -> copy to Imports/<root>/YYYY/MM atomically,
                                                         create the media row, queue indexing
        commit the page's ledger rows + cursor together
    promote the checkpoint when the pass completes

Yaffo never deletes a library file because the source deleted it. See
docs/development/automation-sync-adapters.md (Source run, Edited at the source,
Source deletions).
"""
from __future__ import annotations

import time
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any

import send2trash
from sqlalchemy.orm import Session

from yaffo.background_tasks.events import emit_event
from yaffo.background_tasks.watcher_suppression import record_self_write
from yaffo.common import media_type_for_path
from yaffo.db.models import (
    Automation, MediaItem, SyncItem, EVENT_MEDIA_MODIFIED, MEDIA_STATUS_IMPORTED,
    SYNC_ITEM_DELETED_AT_SOURCE, SYNC_ITEM_FAILED, SYNC_ITEM_IMPORTED, SYNC_ITEM_LINKED_EXISTING,
)
from yaffo.db.repositories import sync_repository
from yaffo.logging_config import get_logger
from yaffo.sync.adapters import LoadedAdapter, source_handler
from yaffo.sync.files import (
    file_sha1, free_bytes, move_into_place, replace_file, sanitize_name, sweep_temps, temp_path,
)
from yaffo.sync.guards import (
    PROBLEM_MEDIA_DIR_MISSING, PROBLEM_UNKNOWN_MEDIA_DIR, folder_source_problem, resolve_import_root,
)
from yaffo.sync.handlers import ERROR_CURSOR_INVALID, ERROR_FATAL, HookContext, SourceHandler
from yaffo.sync.run_common import HookOutputError, RunContext, RunResult, check_error, merge_state, pause, set_runtime
from yaffo.sync.sha1_backfill import backfill_sha1
from yaffo.sync.schemas import (
    CURSOR_LIST, PASS_BACKFILL, PASS_INCREMENTAL, PAUSE_DESTINATION_MISSING, SyncColumn, SyncCursor,
)
from yaffo.utils import job_codes as codes
from yaffo.utils.index_photos import get_exif_data_with_exiftool
from yaffo.utils.photo_dates import get_photo_date_info

logger = get_logger(__name__, 'background_tasks')

# Keep this much free on the library's disk beyond the file being copied.
FREE_SPACE_MARGIN = 200 * 1024 * 1024
UNDATED_FOLDER = "Undated"
ITEM_IMPORT_FAILED = "import_failed"
_RECORD_FIELDS = {"source_id", "filename", "download", "size", "mime", "hash", "taken_at", "location",
                  "deleted", "updated_at"}
_BUILTIN_RECORD_FIELDS = {"local_path"}


class DiskFull(Exception):
    pass


@dataclass
class Counters:
    imported: int = 0
    linked: int = 0
    replaced: int = 0
    unchanged: int = 0
    failed: int = 0
    deleted_at_source: int = 0

    def params(self) -> dict[str, int]:
        return dict(self.__dict__)


def _validate_record(record: Any, builtin: bool) -> dict:
    if not isinstance(record, dict):
        raise HookOutputError("a record must be an object")
    allowed = _RECORD_FIELDS | (_BUILTIN_RECORD_FIELDS if builtin else set())
    unknown = set(record) - allowed
    if unknown:
        raise HookOutputError(f"unknown record fields {sorted(unknown)}")
    if not isinstance(record.get("source_id"), str) or not record["source_id"]:
        raise HookOutputError("record without a source_id")
    if not record.get("deleted") and not isinstance(record.get("filename"), str):
        raise HookOutputError(f"record {record['source_id']} has no filename")
    if record.get("size") is not None and not isinstance(record["size"], int):
        raise HookOutputError(f"record {record['source_id']} has a non-integer size")
    return record


def _capture_folder(path: Path, record: dict) -> tuple[str, ...]:
    """YYYY/MM from the file's own capture date, then the record's hint, then the
    file's modification time."""
    try:
        info = get_photo_date_info(str(path), get_exif_data_with_exiftool(path))
        if info.year and info.month:
            return f"{info.year:04d}", f"{info.month:02d}"
        if info.year:
            return (f"{info.year:04d}",)
    except Exception:  # noqa: BLE001 - an unreadable date just means "use the hint or mtime"
        pass
    local = (record.get("taken_at") or {}).get("local")
    try:
        taken = datetime.fromisoformat(local) if local else datetime.fromtimestamp(path.stat().st_mtime)
        return f"{taken.year:04d}", f"{taken.month:02d}"
    except (OSError, ValueError):
        return (UNDATED_FOLDER,)


class SourceRun:
    def __init__(self, session: Session, automation: Automation, column: SyncColumn,
                 adapter: LoadedAdapter, run: RunContext):
        self.session = session
        self.automation = automation
        self.config = column.config
        self.runtime = column.runtime
        self.adapter = adapter
        self.handler: SourceHandler = source_handler(adapter)
        self.run = run
        self.options = self.config.options
        self.counters = Counters()
        self.started = time.monotonic()
        self.root: Path | None = None
        self.new_paths: list[str] = []
        self.replaced_ids: list[int] = []

    def out_of_time(self) -> bool:
        return time.monotonic() - self.started > self.run.time_budget_seconds or self.run.should_cancel()

    def ctx(self, pass_kind: str) -> HookContext:
        cursor = self.runtime.cursor(CURSOR_LIST)
        state = {**self.runtime.state, "checkpoint": cursor.checkpoint}
        return HookContext(options=self.options, state=state, run_id=self.run.job_id, pass_kind=pass_kind,
                           automation_id=self.automation.id)

    # ---- one record --------------------------------------------------------

    def sha1_of(self, record: dict) -> str | None:
        if record.get("local_path"):
            return file_sha1(Path(record["local_path"]))
        digest = record.get("hash") or {}
        return digest.get("value") if digest.get("algo") == "sha1" else None

    def fetch_to_temp(self, record: dict, folder: Path, ctx: HookContext) -> Path:
        folder.mkdir(parents=True, exist_ok=True)
        size = record.get("size") or 0
        if free_bytes(folder) < size + FREE_SPACE_MARGIN:
            raise DiskFull(str(folder))
        temp = temp_path(folder, str(self.automation.id))
        try:
            self.handler.fetch(record, temp, ctx)
        except Exception:
            temp.unlink(missing_ok=True)
            raise
        return temp

    def import_new(self, record: dict, sha1: str | None, ctx: HookContext) -> SyncItem:
        assert self.root is not None
        temp = self.fetch_to_temp(record, self.root, ctx)
        if sha1 is None:
            sha1 = file_sha1(temp)
            existing = self.session.query(MediaItem).filter(MediaItem.sha1 == sha1).first()
            if existing is not None:
                temp.unlink()
                return self.link(record, existing, sha1)
        folder = self.root.joinpath(*_capture_folder(temp, record))
        final = move_into_place(temp, folder, sanitize_name(record["filename"]))
        record_self_write(final)
        media = self.session.query(MediaItem).filter(MediaItem.full_file_path == str(final)).first()
        if media is None:
            media = MediaItem(full_file_path=str(final), media_type=media_type_for_path(final),
                              status=MEDIA_STATUS_IMPORTED, sha1=sha1)
            self.session.add(media)
            self.session.flush()
            self.new_paths.append(str(final))
        self.counters.imported += 1
        return SyncItem(automation_id=self.automation.id, item_key=record["source_id"], media_item_id=media.id,
                        remote_id=record["source_id"], fingerprint=record.get("updated_at"),
                        status=SYNC_ITEM_IMPORTED, attempts=1, run_id=self.run.job_id,
                        details={"relative_path": final.relative_to(self.root).as_posix(), "sha1": sha1})

    def link(self, record: dict, media: MediaItem, sha1: str) -> SyncItem:
        self.counters.linked += 1
        return SyncItem(automation_id=self.automation.id, item_key=record["source_id"], media_item_id=media.id,
                        remote_id=record["source_id"], fingerprint=record.get("updated_at"),
                        status=SYNC_ITEM_LINKED_EXISTING, attempts=1, run_id=self.run.job_id,
                        details={"sha1": sha1})

    def replace_edited(self, row: SyncItem, record: dict, sha1: str | None, ctx: HookContext) -> None:
        """The source has a new version of a file Yaffo imported: replace the
        library's copy at its current path (the old version goes to the trash), keep
        the media item (its id, albums, tags), and re-index it."""
        media = self.session.get(MediaItem, row.media_item_id) if row.media_item_id else None
        if media is None or not Path(media.full_file_path).is_file():
            return
        current = Path(media.full_file_path)
        temp = self.fetch_to_temp(record, current.parent, ctx)
        try:
            new_sha1 = sha1 or file_sha1(temp)
            if new_sha1 == (row.details or {}).get("sha1"):
                return
            send2trash.send2trash(str(current))
            replace_file(temp, current, temp_tag=str(self.automation.id))
        finally:
            temp.unlink(missing_ok=True)
        record_self_write(current)
        media.sha1 = new_sha1
        row.details = {**(row.details or {}), "sha1": new_sha1}
        self.replaced_ids.append(media.id)
        self.counters.replaced += 1

    def process(self, record: dict, known: SyncItem | None, ctx: HookContext, seen: dict[str, MediaItem]) -> None:
        source_id = record["source_id"]
        if record.get("deleted"):
            if known is None:
                self.session.add(SyncItem(automation_id=self.automation.id, item_key=source_id,
                                          remote_id=source_id, status=SYNC_ITEM_DELETED_AT_SOURCE, attempts=0))
            elif known.status != SYNC_ITEM_DELETED_AT_SOURCE:
                known.status = SYNC_ITEM_DELETED_AT_SOURCE
            self.counters.deleted_at_source += 1
            return
        if known is not None and known.fingerprint == record.get("updated_at") and known.status != SYNC_ITEM_FAILED:
            if known.status == SYNC_ITEM_DELETED_AT_SOURCE:
                known.status = SYNC_ITEM_IMPORTED if known.media_item_id else SYNC_ITEM_LINKED_EXISTING
            self.counters.unchanged += 1
            return
        sha1 = self.sha1_of(record)
        if known is not None and known.status != SYNC_ITEM_FAILED:
            if sha1 is None or sha1 != (known.details or {}).get("sha1"):
                self.replace_edited(known, record, sha1, ctx)
            else:
                self.counters.unchanged += 1
            known.fingerprint = record.get("updated_at")
            return
        existing = None
        if sha1 is not None:
            existing = seen.get(sha1) or self.session.query(MediaItem).filter(MediaItem.sha1 == sha1).first()
        if existing is not None:
            row = self.link(record, existing, sha1)
        else:
            row = self.import_new(record, sha1, ctx)
            if sha1 is not None and row.media_item_id:
                seen[sha1] = self.session.get(MediaItem, row.media_item_id)
        if known is not None:
            for name in ("media_item_id", "remote_id", "fingerprint", "status", "run_id", "details"):
                setattr(known, name, getattr(row, name))
            known.error_code = None
            known.attempts += 1
        else:
            self.session.add(row)

    # ---- pages -------------------------------------------------------------

    def run_page(self, records: list[dict], ctx: HookContext) -> None:
        known = sync_repository.rows_by_key(self.session, self.automation.id,
                                            [record["source_id"] for record in records])
        seen: dict[str, MediaItem] = {}
        for record in records:
            try:
                self.process(record, known.get(record["source_id"]), ctx, seen)
            except DiskFull:
                raise
            except (OSError, ValueError) as e:
                # One unreadable or vanished file is one failed item. Nothing for it
                # was added to the session before the file operation failed.
                logger.warning(f"sync '{self.automation.slug}': could not import {record['source_id']}: {e}")
                self.mark_failed(record, known.get(record["source_id"]))
                self.counters.failed += 1

    def mark_failed(self, record: dict, known: SyncItem | None) -> None:
        row = known
        if row is None:
            row = SyncItem(automation_id=self.automation.id, item_key=record["source_id"],
                           remote_id=record["source_id"], status=SYNC_ITEM_FAILED, attempts=0)
            self.session.add(row)
        row.status, row.error_code = SYNC_ITEM_FAILED, ITEM_IMPORT_FAILED
        row.attempts = (row.attempts or 0) + 1

    def commit_page(self, cursor: SyncCursor, state_patch: dict | None) -> None:
        state = merge_state(self.runtime.state, state_patch)
        self.runtime = replace(self.runtime.with_cursor(CURSOR_LIST, cursor), state=state)
        set_runtime(self.session, self.automation, self.runtime)
        if self.new_paths and self.run.index_files is not None:
            self.run.index_files(self.session, list(self.new_paths), self.automation.id)
        self.new_paths = []
        if self.replaced_ids:
            if self.run.reindex_items is not None:
                self.run.reindex_items(self.session, list(self.replaced_ids))
            emit_event(EVENT_MEDIA_MODIFIED, {"media_item_ids": list(self.replaced_ids)})
        self.replaced_ids = []

    def execute(self) -> RunResult:
        problem = self.handler.source_problem(self.options)
        if problem:
            return RunResult(outcome=problem)
        root, root_problem = resolve_import_root(self.session, self.config.import_root or {})
        if root_problem in (PROBLEM_UNKNOWN_MEDIA_DIR, PROBLEM_MEDIA_DIR_MISSING):
            return pause(self.session, self.automation, self.runtime, PAUSE_DESTINATION_MISSING)
        if root is None:
            return RunResult(outcome=codes.OUTCOME_SYNC_NOT_RUN, problem=codes.PROBLEM_SYNC_INVALID_CONFIG,
                             error=f"import root problem: {root_problem}")
        if self.adapter.is_builtin and "folder" in self.options:
            guard = folder_source_problem(self.session, Path(self.options["folder"]))
            if guard:
                return RunResult(outcome=codes.OUTCOME_SYNC_NOT_RUN, problem=codes.PROBLEM_SYNC_INVALID_CONFIG,
                                 error=f"source folder problem: {guard}")
        root.mkdir(parents=True, exist_ok=True)
        self.root = root
        sweep_temps(root, str(self.automation.id))
        # Dedupe needs every library photo's hash; photos indexed before hashes
        # existed get theirs now (the rest of the budget is left for importing).
        backfill_sha1(self.session, self.run.time_budget_seconds / 3)

        cursor = self.runtime.cursor(CURSOR_LIST)
        pass_kind = cursor.pass_kind if cursor.next_cursor else (
            PASS_INCREMENTAL if cursor.last_completed_at else PASS_BACKFILL)
        if not cursor.next_cursor:
            cursor = replace(cursor, pass_kind=pass_kind, pass_started_at=self.run.clock().isoformat())
        restarted = False
        try:
            while True:
                if self.out_of_time():
                    break
                ctx = self.ctx(pass_kind)
                envelope = self.handler.list_page(cursor.next_cursor, ctx)
                if not isinstance(envelope, dict):
                    raise HookOutputError("list_page must return an object")
                unknown = set(envelope) - {"records", "next_cursor", "checkpoint", "state_patch", "error"}
                if unknown:
                    raise HookOutputError(f"unknown keys {sorted(unknown)}")
                error = check_error(envelope.get("error"))
                if error and error["code"] == ERROR_CURSOR_INVALID and not restarted:
                    restarted = True
                    cursor = replace(cursor, next_cursor=None)
                    continue
                records = [_validate_record(r, self.adapter.is_builtin) for r in envelope.get("records") or []]
                self.run_page(records, ctx)
                next_cursor = envelope.get("next_cursor")
                if next_cursor is None:
                    cursor = SyncCursor(pass_kind=PASS_INCREMENTAL, next_cursor=None,
                                        checkpoint=envelope.get("checkpoint") or cursor.checkpoint,
                                        pass_started_at=None, last_completed_at=self.run.clock().isoformat())
                else:
                    cursor = replace(cursor, next_cursor=str(next_cursor))
                self.commit_page(cursor, envelope.get("state_patch"))
                if error:
                    return self.result(problem=codes.PROBLEM_SYNC_STOPPED, problem_params={"code": error["code"]},
                                       error=error.get("message"), failed=error["code"] == ERROR_FATAL)
                if next_cursor is None:
                    break
        except DiskFull as e:
            self.session.rollback()
            return self.result(problem=codes.PROBLEM_SYNC_DISK_FULL, error=f"not enough free space in {e}")
        except HookOutputError as e:
            self.session.rollback()
            return self.result(problem=codes.PROBLEM_SYNC_STOPPED, problem_params={"code": ERROR_FATAL},
                               error=f"the service's code returned something invalid: {e}", failed=True)
        if self.counters.failed:
            return self.result(problem=codes.PROBLEM_SYNC_ITEMS_FAILED, problem_params={"count": self.counters.failed})
        return self.result()

    def result(self, problem: str | None = None, problem_params: dict | None = None,
               error: str | None = None, failed: bool = False) -> RunResult:
        return RunResult(outcome=codes.OUTCOME_SYNC_IMPORTED, params=self.counters.params(), problem=problem,
                         problem_params=problem_params or {}, error=error, failed_run=failed)


def run_source(session: Session, automation: Automation, column: SyncColumn, adapter: LoadedAdapter,
               run: RunContext) -> RunResult:
    return SourceRun(session, automation, column, adapter, run).execute()
