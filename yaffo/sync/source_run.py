"""One run of a source sync: bring new files from the source into the import root.

    loop pages from the stored cursor (resumes mid-pass):
        record seen with the same updated_at          -> skip
        known record, new updated_at, same content    -> just note the new updated_at
        known record, new content                     -> replace the library's copy (old one to the trash)
        content already in the library (sha1)         -> link, don't copy
        otherwise                                     -> copy to Imports/<root>/YYYY/MM atomically,
                                                         create the media row, queue indexing
        commit each record so downloads do not hold SQLite's write lock;
        commit the page cursor after its records
    promote the checkpoint when the pass completes

Starlark adapters (OneDrive) go through the same loop. Their hook errors are
applied by code: `auth` marks the account `needs_sign_in`, `rate_limited` and
`retry_later` stop the run and schedule the next one, `not_found` on the run's first
call pauses the sync (`option_missing`), and `cursor_invalid` restarts the pass.
A backfill that runs out of time schedules its own continuation. Items whose
download failed keep their record and are retried at the start of later runs (a
delta feed won't list them again).

Yaffo never deletes a library file because the source deleted it. See
docs/development/automation-sync-adapters.md (Source run, Edited at the source,
Source deletions).
"""
from __future__ import annotations

import time
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import unquote

import send2trash
from sqlalchemy.orm import Session

from yaffo.background_tasks.events import emit_event
from yaffo.background_tasks.watcher_suppression import record_self_write
from yaffo.common import media_type_for_path
from yaffo.db.models import (
    Automation, MediaItem, SyncItem, EVENT_MEDIA_MODIFIED, MEDIA_STATUS_IMPORTED,
    SYNC_ITEM_DELETED_AT_SOURCE, SYNC_ITEM_FAILED, SYNC_ITEM_IMPORTED, SYNC_ITEM_LINKED_EXISTING, SYNC_ITEM_SKIPPED,
)
from yaffo.db.repositories import sync_repository
from yaffo.logging_config import get_logger
from yaffo.sync import access, connections
from yaffo.sync.adapters import LoadedAdapter, source_handler
from yaffo.sync.files import (
    file_sha1, free_bytes, move_into_place, replace_file, sanitize_name, stable_temp_path, sweep_temps, temp_path,
)
from yaffo.sync.guards import (
    PROBLEM_MEDIA_DIR_MISSING, PROBLEM_UNKNOWN_MEDIA_DIR, folder_source_problem, resolve_import_root,
)
from yaffo.sync.handlers import (
    ERROR_AUTH, ERROR_CURSOR_INVALID, ERROR_FATAL, ERROR_NOT_FOUND, ERROR_RATE_LIMITED, ERROR_RETRY_LATER,
    STOP_OUT_OF_TIME, HookContext, SourceHandler, SourceItemSkipped, SourceStop,
)
from yaffo.sync.run_common import (
    HookOutputError, RunContext, RunResult, check_error, merge_state, pause, schedule_continuation, set_runtime,
    stop,
)
from yaffo.sync.starlark_adapter import HookFailed
from yaffo.sync.web import WebClient, url_hash
from yaffo.sync.sha1_backfill import backfill_sha1
from yaffo.sync.schemas import (
    CURSOR_LIST, DEFAULT_IMPORT_LAYOUT, LAYOUT_FLAT, LAYOUT_SOURCE_FOLDERS, LAYOUT_YEAR, PASS_BACKFILL,
    PASS_INCREMENTAL, PAUSE_DESTINATION_MISSING, PAUSE_OPTION_MISSING, RUNTIME_NEEDS_SIGN_IN, SyncColumn, SyncCursor,
)
from yaffo.utils import job_codes as codes
from yaffo.utils.index_photos import get_exif_data_with_exiftool
from yaffo.utils.photo_dates import get_photo_date_info

logger = get_logger(__name__, 'background_tasks')

# Keep this much free on the library's disk beyond the file being copied.
FREE_SPACE_MARGIN = 200 * 1024 * 1024
UNDATED_FOLDER = "Undated"
ITEM_IMPORT_FAILED = "import_failed"
_RECORD_FIELDS = {"source_id", "filename", "folder", "download", "size", "mime", "hash", "taken_at", "location",
                  "deleted", "updated_at"}
# Host-only record fields of built-in sources: the Folder source's file, the peer
# source's pull handle (yaffo/sync/peer_source.py).
_BUILTIN_RECORD_FIELDS = {"local_path", "peer"}
# A run that stopped early is continued this soon (time budget) or this late
# (a transient failure at the service).
CONTINUE_AFTER_SECONDS = 60
RETRY_LATER_SECONDS = 15 * 60
# Failed downloads are retried at the start of this many later runs.
MAX_ITEM_ATTEMPTS = 5


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


def validate_record(record: Any, builtin: bool) -> dict:
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
    if record.get("size") is not None and (not isinstance(record["size"], int) or record["size"] < 0):
        raise HookOutputError(f"record {record['source_id']} has an invalid size")
    for name, kind in (("updated_at", str), ("mime", str), ("deleted", bool), ("folder", str)):
        if record.get(name) is not None and not isinstance(record[name], kind):
            raise HookOutputError(f"record {record['source_id']}: {name} has the wrong type")
    download = record.get("download")
    if download is not None and (not isinstance(download, dict) or not isinstance(download.get("url"), str)
                                 or set(download) - {"url", "headers"}
                                 or not isinstance(download.get("headers") or {}, dict)):
        raise HookOutputError(f"record {record['source_id']}: download must be {{url, headers}}")
    digest = record.get("hash")
    if digest is not None and (not isinstance(digest, dict) or set(digest) != {"algo", "value"}
                               or not all(isinstance(v, str) for v in digest.values())):
        raise HookOutputError(f"record {record['source_id']}: hash must be {{algo, value}}")
    taken = record.get("taken_at")
    if taken is not None and (not isinstance(taken, dict) or set(taken) - {"local", "utc", "offset"}
                              or not all(isinstance(v, str) for v in taken.values() if v is not None)):
        raise HookOutputError(f"record {record['source_id']}: taken_at must be {{local, utc, offset}}")
    location = record.get("location")
    if location is not None and (not isinstance(location, dict) or set(location) != {"lat", "lon"}
                                 or not all(isinstance(v, (int, float)) and not isinstance(v, bool)
                                            for v in location.values())):
        raise HookOutputError(f"record {record['source_id']}: location must be {{lat, lon}}")
    return record


def _hinted_date(record: dict) -> str | None:
    """The record's capture time as Yaffo stores dates (naive local wall clock):
    `local` as given, or `utc` shifted by a known `offset`. File EXIF wins at index."""
    taken = record.get("taken_at") or {}
    try:
        if taken.get("local"):
            return datetime.fromisoformat(taken["local"]).replace(tzinfo=None).isoformat()
        if taken.get("utc") and taken.get("offset"):
            moment = datetime.fromisoformat(taken["utc"].replace("Z", "+00:00"))
            sign = -1 if taken["offset"].startswith("-") else 1
            hours, minutes = taken["offset"].lstrip("+-").split(":")
            return (moment + sign * timedelta(hours=int(hours), minutes=int(minutes))).replace(tzinfo=None).isoformat()
    except (ValueError, AttributeError):
        return None
    return None


def _source_folder(record: dict) -> tuple[str, ...]:
    """The record's folder under the chosen source folder, made safe: each part
    sanitized, nothing that could climb out of the import root."""
    raw = unquote(record.get("folder") or "") if "%" in (record.get("folder") or "") else (record.get("folder") or "")
    parts = []
    for part in raw.replace("\\", "/").split("/"):
        if part.strip() in ("", ".", ".."):
            continue
        parts.append(sanitize_name(part.strip()))
    return tuple(parts)


def destination_folder(layout: str, path: Path, record: dict) -> tuple[str, ...]:
    """Where under the import root a new file goes, by the sync's layout."""
    if layout == LAYOUT_FLAT:
        return ()
    if layout == LAYOUT_SOURCE_FOLDERS:
        return _source_folder(record)
    dated = _capture_folder(path, record)
    return dated[:1] if layout == LAYOUT_YEAR else dated


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
        self.client: WebClient | None = None
        self.layout = (self.config.import_root or {}).get("layout", DEFAULT_IMPORT_LAYOUT)

    def out_of_time(self) -> bool:
        return time.monotonic() - self.started > self.run.time_budget_seconds or self.run.should_cancel()

    def ctx(self, pass_kind: str) -> HookContext:
        cursor = self.runtime.cursor(CURSOR_LIST)
        state = {**self.runtime.state, "checkpoint": cursor.checkpoint}
        return HookContext(options=self.options, state=state, run_id=self.run.job_id, pass_kind=pass_kind,
                           automation_id=self.automation.id, extra={"should_stop": self.out_of_time},
                           filters=self.config.source_filters)

    # ---- one record --------------------------------------------------------

    def sha1_of(self, record: dict) -> str | None:
        if record.get("local_path"):
            return file_sha1(Path(record["local_path"]))
        digest = record.get("hash") or {}
        return digest.get("value") if digest.get("algo") == "sha1" else None

    def temp_for(self, record: dict, folder: Path) -> Path:
        """Where a record is fetched to. A resumable source gets the same name every
        run, so a partial download left by an earlier run is picked up again."""
        if self.handler.resumable_fetch:
            return stable_temp_path(folder, str(self.automation.id), record["source_id"])
        return temp_path(folder, str(self.automation.id))

    def fetch_to_temp(self, record: dict, folder: Path, ctx: HookContext) -> Path:
        folder.mkdir(parents=True, exist_ok=True)
        size = record.get("size") or 0
        if free_bytes(folder) < size + FREE_SPACE_MARGIN:
            raise DiskFull(str(folder))
        temp = self.temp_for(record, folder)
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
        folder = self.root.joinpath(*destination_folder(self.layout, temp, record))
        final = move_into_place(temp, folder, sanitize_name(record["filename"]))
        record_self_write(final)
        media = self.session.query(MediaItem).filter(MediaItem.full_file_path == str(final)).first()
        if media is None:
            location = record.get("location") or {}
            media = MediaItem(full_file_path=str(final), media_type=media_type_for_path(final),
                              status=MEDIA_STATUS_IMPORTED, sha1=sha1, date_taken=_hinted_date(record),
                              latitude=location.get("lat"), longitude=location.get("lon"))
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
                                          remote_id=source_id, status=SYNC_ITEM_DELETED_AT_SOURCE, attempts=0,
                                          details={"filename": record.get("filename") or None}))
            elif known.status != SYNC_ITEM_DELETED_AT_SOURCE:
                known.status = SYNC_ITEM_DELETED_AT_SOURCE
            self.counters.deleted_at_source += 1
            return
        if known is not None and known.fingerprint == record.get("updated_at") and known.status != SYNC_ITEM_FAILED:
            if known.status == SYNC_ITEM_DELETED_AT_SOURCE:
                known.status = SYNC_ITEM_IMPORTED if known.media_item_id else SYNC_ITEM_LINKED_EXISTING
            elif (known.status == SYNC_ITEM_IMPORTED and known.media_item_id
                  and not (known.details or {}).get("index_queued")):
                # A previous attempt may have committed this record and crashed
                # before commit_page queued indexing. The page cursor still points
                # here, so queue it when the replay finds it unindexed.
                media = self.session.get(MediaItem, known.media_item_id)
                if media is not None and media.status == MEDIA_STATUS_IMPORTED:
                    self.new_paths.append(media.full_file_path)
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

    def plan_fetches(self, records: list[dict], known: dict[str, SyncItem], ctx: HookContext) -> None:
        """Tell the handler which of this page's records will be downloaded as new
        files, so it can work ahead on them. Only new or failed records whose content
        isn't already in the library: everything else is skipped or linked without a
        download, and an edited file lands next to its old copy, not in the root."""
        if type(self.handler).plan_fetches is SourceHandler.plan_fetches or self.root is None:
            return
        hashes = {self.sha1_of(r) for r in records if not r.get("local_path") and r.get("hash")} - {None}
        in_library = {sha1 for sha1, in self.session.query(MediaItem.sha1).filter(MediaItem.sha1.in_(hashes))} \
            if hashes else set()
        upcoming = []
        for record in records:
            row = known.get(record["source_id"])
            if record.get("deleted") or (row is not None and row.status != SYNC_ITEM_FAILED):
                continue
            if self.sha1_of(record) in in_library:
                continue
            upcoming.append((record, self.temp_for(record, self.root)))
        self.handler.plan_fetches(upcoming, ctx)

    def run_page(self, records: list[dict], ctx: HookContext) -> None:
        known = sync_repository.rows_by_key(self.session, self.automation.id,
                                            [record["source_id"] for record in records])
        seen: dict[str, MediaItem] = {}
        self.plan_fetches(records, known, ctx)
        if self.client is not None:
            self.client.mid_page = True
        try:
            for record in records:
                try:
                    self.process(record, known.get(record["source_id"]), ctx, seen)
                except DiskFull:
                    raise
                except SourceItemSkipped as e:
                    self.mark_skipped(record, known.get(record["source_id"]), e.error_code)
                except (OSError, ValueError) as e:
                    # One unreadable or vanished file is one failed item. Nothing for it
                    # was added to the session before the file operation failed.
                    logger.warning(f"sync '{self.automation.slug}': could not import {record['source_id']}: {e}")
                    self.mark_failed(record, known.get(record["source_id"]))
                    self.counters.failed += 1
                # A page can contain hundreds of downloads. Once the first media
                # row is flushed, SQLite holds its writer lock until commit; without
                # this boundary the web process cannot even mark the run cancelled.
                # The cursor still advances only after the page, so a crash replays
                # these already committed records through the idempotent ledger.
                self.session.commit()
        finally:
            if self.client is not None:
                self.client.mid_page = False

    def mark_failed(self, record: dict, known: SyncItem | None) -> None:
        row = known
        if row is None:
            row = SyncItem(automation_id=self.automation.id, item_key=record["source_id"],
                           remote_id=record["source_id"], status=SYNC_ITEM_FAILED, attempts=0)
            self.session.add(row)
        row.status, row.error_code = SYNC_ITEM_FAILED, ITEM_IMPORT_FAILED
        row.attempts = (row.attempts or 0) + 1
        row.details = {**(row.details or {}), "filename": record.get("filename")}
        if not self.adapter.is_builtin:
            # A delta feed won't list it again: keep the record to retry it.
            row.details = {**(row.details or {}), "record": record}

    def mark_skipped(self, record: dict, known: SyncItem | None, error_code: str | None) -> None:
        row = known
        if row is None:
            row = SyncItem(automation_id=self.automation.id, item_key=record["source_id"],
                           remote_id=record["source_id"], status=SYNC_ITEM_SKIPPED, attempts=0)
            self.session.add(row)
        row.status, row.error_code = SYNC_ITEM_SKIPPED, error_code
        row.attempts = (row.attempts or 0) + 1
        row.details = {**(row.details or {}), "filename": record.get("filename")}

    def retry_failed(self, ctx: HookContext) -> None:
        """Retry items whose download failed in earlier runs, from their kept records."""
        rows = [row for row in sync_repository.rows(self.session, self.automation.id, [SYNC_ITEM_FAILED])
                if (row.details or {}).get("record") and (row.attempts or 0) < MAX_ITEM_ATTEMPTS]
        if not rows:
            return
        records = []
        for row in rows:
            try:
                records.append(validate_record(row.details["record"], builtin=False))
            except HookOutputError:
                continue
        self.run_page(records, ctx)
        for row in rows:
            if row.status != SYNC_ITEM_FAILED and row.details and "record" in row.details:
                row.details = {k: v for k, v in row.details.items() if k != "record"}
        self.commit_page(self.runtime.cursor(CURSOR_LIST), None)

    def vouched(self, cursor: SyncCursor) -> list[str]:
        """Hashes of the stored cursor and checkpoint that the next run may fetch:
        URLs a granted call returned (now, or vouched for by an earlier run)."""
        if self.client is None:
            return []
        return [url_hash(value) for value in (cursor.next_cursor, cursor.checkpoint)
                if value and self.client.vouches(value)]

    def commit_page(self, cursor: SyncCursor, state_patch: dict | None) -> None:
        state = merge_state(self.runtime.state, state_patch)
        self.runtime = replace(self.runtime.with_cursor(CURSOR_LIST, cursor), state=state,
                               followups=self.vouched(cursor))
        sync_repository.save_runtime(self.automation, self.runtime)
        if self.new_paths and self.run.index_files is not None:
            paths = list(dict.fromkeys(self.new_paths))
            for row in (self.session.query(SyncItem)
                        .join(MediaItem, MediaItem.id == SyncItem.media_item_id)
                        .filter(SyncItem.automation_id == self.automation.id,
                                SyncItem.status == SYNC_ITEM_IMPORTED,
                                MediaItem.full_file_path.in_(paths))):
                row.details = {**(row.details or {}), "index_queued": True}
            self.run.index_files(self.session, paths, self.automation.id)
        self.new_paths = []
        if self.replaced_ids:
            if self.run.reindex_items is not None:
                self.run.reindex_items(self.session, list(self.replaced_ids))
            emit_event(EVENT_MEDIA_MODIFIED, {"media_item_ids": list(self.replaced_ids)})
        self.replaced_ids = []
        self.session.commit()

    def execute(self) -> RunResult:
        try:
            return self._execute()
        finally:
            try:
                self.handler.finish_run(self.ctx(PASS_INCREMENTAL))
            except Exception:  # noqa: BLE001 — releasing must never hide the run's result
                logger.exception(f"sync '{self.automation.slug}': finishing the run failed")

    def _execute(self) -> RunResult:
        pause_code = self.handler.source_pause(self.options)
        if pause_code:
            return pause(self.session, self.automation, self.runtime, pause_code)
        problem = self.handler.source_problem(self.options)
        if problem:
            return RunResult(outcome=problem, params=self.handler.summary_params())
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
        self.client = access.attach(self.session, self.adapter, self.config, vouched=self.runtime.followups,
                                    should_cancel=self.run.should_cancel)

        cursor = self.runtime.cursor(CURSOR_LIST)
        pass_kind = cursor.pass_kind if cursor.next_cursor else (
            PASS_INCREMENTAL if cursor.last_completed_at else PASS_BACKFILL)
        if not cursor.next_cursor:
            cursor = replace(cursor, pass_kind=pass_kind, pass_started_at=self.run.clock().isoformat())
        restarted = False
        first_call = True
        try:
            if not self.adapter.is_builtin:
                self.retry_failed(self.ctx(pass_kind))
            while True:
                if self.out_of_time():
                    if cursor.next_cursor and not self.run.should_cancel():
                        schedule_continuation(self.session, self.automation, CONTINUE_AFTER_SECONDS)
                    break
                ctx = self.ctx(pass_kind)
                envelope = self.handler.list_page(cursor.next_cursor, ctx)
                if not isinstance(envelope, dict):
                    raise HookOutputError("list_page must return an object")
                unknown = set(envelope) - {"records", "next_cursor", "checkpoint", "state_patch", "error",
                                           "progress"}
                if unknown:
                    raise HookOutputError(f"unknown keys {sorted(unknown)}")
                progress = envelope.get("progress")
                if progress is not None and (
                    not isinstance(progress, dict) or set(progress) != {"done", "total"}
                    or not all(isinstance(progress[key], int) and not isinstance(progress[key], bool)
                               for key in ("done", "total"))
                    or not 0 <= progress["done"] <= progress["total"]
                ):
                    raise HookOutputError("progress must contain valid done and total counts")
                error = check_error(envelope.get("error"))
                if self.client is not None and self.client.auth_failed:
                    # Whatever the adapter made of the failed call, the account is the problem.
                    error = {"code": ERROR_AUTH, "message": "the provider refused the account's credentials"}
                if error and error["code"] == ERROR_CURSOR_INVALID and not restarted:
                    restarted = True
                    cursor = replace(cursor, next_cursor=None,
                                     checkpoint=None if not cursor.next_cursor else cursor.checkpoint)
                    self.runtime = self.runtime.with_cursor(CURSOR_LIST, cursor)
                    continue
                if error and error["code"] == ERROR_AUTH:
                    return self.needs_sign_in(error)
                if error and error["code"] == ERROR_NOT_FOUND and first_call:
                    self.session.rollback()
                    return pause(self.session, self.automation, self.runtime, PAUSE_OPTION_MISSING)
                first_call = False
                records = [validate_record(r, self.adapter.is_builtin) for r in envelope.get("records") or []]
                self.run_page(records, ctx)
                next_cursor = envelope.get("next_cursor")
                if error is None and next_cursor is None:
                    cursor = SyncCursor(pass_kind=PASS_INCREMENTAL, next_cursor=None,
                                        checkpoint=envelope.get("checkpoint") or cursor.checkpoint,
                                        pass_started_at=None, last_completed_at=self.run.clock().isoformat())
                elif next_cursor is not None:
                    if not isinstance(next_cursor, str):
                        raise HookOutputError("next_cursor must be text")
                    cursor = replace(cursor, next_cursor=next_cursor)
                self.commit_page(cursor, envelope.get("state_patch"))
                if progress is not None and self.run.on_progress:
                    self.run.on_progress(progress["done"], progress["total"])
                if error:
                    return self.stopped(error)
                if next_cursor is None:
                    break
        except SourceStop as e:
            # Keep what this page already imported; the cursor stays at the page's
            # start, so the next run lists it again (the done records are unchanged).
            self.commit_page(cursor, None)
            return self.interrupted(e)
        except DiskFull as e:
            self.session.rollback()
            return self.result(problem=codes.PROBLEM_SYNC_DISK_FULL, error=f"not enough free space in {e}")
        except (HookOutputError, HookFailed) as e:
            self.session.rollback()
            return self.result(problem=codes.PROBLEM_SYNC_STOPPED, problem_params={"code": ERROR_FATAL},
                               error=f"the service's code returned something invalid: {e}", failed=True)
        if self.counters.failed:
            return self.result(problem=codes.PROBLEM_SYNC_ITEMS_FAILED, problem_params={"count": self.counters.failed})
        return self.result()

    def needs_sign_in(self, error: dict) -> RunResult:
        """The provider refused the account: every sync using it shows "Sign in
        again". Nothing from this page was applied."""
        self.session.rollback()
        if self.config.connection_key:
            connections.mark_needs_sign_in(self.session.get_bind(), self.config.connection_key)
        result = stop(self.session, self.automation, self.runtime, RUNTIME_NEEDS_SIGN_IN,
                      codes.PROBLEM_SYNC_NEEDS_SIGN_IN, error=error.get("message"))
        result.outcome = codes.OUTCOME_SYNC_IMPORTED
        result.params = self.counters.params()
        result.problem_params = {"reason": access.ACCOUNT_NEEDS_SIGN_IN}
        return result

    def interrupted(self, stop_: SourceStop) -> RunResult:
        if stop_.pause_code:
            result = pause(self.session, self.automation, self.runtime, stop_.pause_code)
            result.params = {**self.counters.params(), **self.handler.summary_params()}
            return result
        if stop_.outcome:
            if not any(self.counters.params().values()):
                return RunResult(outcome=stop_.outcome, params={**self.handler.summary_params(), **stop_.params},
                                 error=stop_.message or None)
            return self.result(problem=codes.PROBLEM_SYNC_INTERRUPTED,
                               problem_params={"outcome": stop_.outcome, **stop_.params},
                               error=stop_.message or None)
        if stop_.error_code == STOP_OUT_OF_TIME:
            if not self.run.should_cancel():
                schedule_continuation(self.session, self.automation, CONTINUE_AFTER_SECONDS)
            return self.result()
        return self.stopped({"code": stop_.error_code or ERROR_FATAL, "message": stop_.message})

    def stopped(self, error: dict) -> RunResult:
        """A hook error after its page was applied: stop, and come back later."""
        code = error["code"]
        if code == ERROR_RATE_LIMITED:
            wait = error.get("retry_after_s")
            schedule_continuation(self.session, self.automation,
                                  wait if isinstance(wait, (int, float)) and wait > 0 else CONTINUE_AFTER_SECONDS)
        elif code in (ERROR_RETRY_LATER, ERROR_NOT_FOUND):
            schedule_continuation(self.session, self.automation, RETRY_LATER_SECONDS)
        return self.result(problem=codes.PROBLEM_SYNC_STOPPED, problem_params={"code": code},
                           error=error.get("message"), failed=code == ERROR_FATAL)

    def result(self, problem: str | None = None, problem_params: dict | None = None,
               error: str | None = None, failed: bool = False) -> RunResult:
        return RunResult(outcome=codes.OUTCOME_SYNC_IMPORTED,
                         params={**self.counters.params(), **self.handler.summary_params()}, problem=problem,
                         problem_params=problem_params or {}, error=error, failed_run=failed)


def run_source(session: Session, automation: Automation, column: SyncColumn, adapter: LoadedAdapter,
               run: RunContext) -> RunResult:
    return SourceRun(session, automation, column, adapter, run).execute()
