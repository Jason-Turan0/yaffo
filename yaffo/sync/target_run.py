"""One run of a target sync: make the target hold the desired set.

    desired   = selection()
    recover   = pending rows a crashed run left behind -> reconcile (present = sent)
    drift     = a weekly reconcile of sent rows (missing = removed_remotely)
    to_remove = sent rows no longer desired + rows marked `removing` by a delete
    to_push   = desired items with no sent row
    each batch: write intent rows (pending) -> commit -> call the handler ->
                commit results + state_patch together

Rows are keyed by photo when the target has one destination and by photo:target
when it routes to many. A routing change on a target without `update` is a remove
plus a push. Photos removed remotely by the user are not re-sent. See
docs/development/automation-sync-adapters.md (Target run, Photos deleted from Yaffo).
"""
from __future__ import annotations

import shutil
import tempfile
import time
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from itertools import islice
from pathlib import Path
from typing import Any, Iterator

from sqlalchemy.orm import Session

from yaffo.db.models import (
    Automation, MediaItem, SyncItem, SYNC_HANDLER_FOLDER_TARGET,
    SYNC_ITEM_CANT_REMOVE, SYNC_ITEM_FAILED, SYNC_ITEM_PENDING, SYNC_ITEM_REMOVED, SYNC_ITEM_REMOVED_REMOTELY,
    SYNC_ITEM_REMOVING, SYNC_ITEM_SENT,
)
from yaffo.db.repositories import sync_repository
from yaffo.sync import access, connections
from yaffo.logging_config import get_logger
from yaffo.sync.adapters import LoadedAdapter, target_handler
from yaffo.sync.convert import UnsupportedFormat, convert_to_jpeg, mime_for, needs_conversion
from yaffo.sync.files import sweep_temps
from yaffo.sync.guards import folder_target_problem
from yaffo.sync.handlers import (
    ERROR_AUTH, ERROR_CHANGED, ERROR_FATAL, ERROR_NOT_FOUND, ERROR_RATE_LIMITED, ERROR_REJECTED, ERROR_RETRY_LATER,
    MISSING, PRESENT, TARGETS_MANY, HookContext, TargetCapabilities, TargetHandler,
)
from yaffo.sync.run_common import (
    HookOutputError, RunContext, RunResult, check_results, merge_state, pause, set_runtime, stop,
    schedule_continuation,
)
from yaffo.sync.schemas import (
    CURSOR_RECONCILE, PASS_INCREMENTAL, PAUSE_SELECTION_MISSING, RUNTIME_NEEDS_SIGN_IN, SyncColumn, SyncCursor,
)
from yaffo.sync.selection import Desired, SelectionMissing, resolve_selection
from yaffo.sync.target_layout import one_drive_targets
from yaffo.sync.folder_target import LAYOUT_PER_TARGET
from yaffo.sync.starlark_adapter import HookFailed
from yaffo.utils import job_codes as codes

logger = get_logger(__name__, 'background_tasks')

RECONCILE_EVERY = timedelta(days=7)

# Item-level failure codes the engine records itself.
ITEM_NOT_SENT = "not_sent"
ITEM_SOURCE_MISSING = "source_missing"
ITEM_UNSUPPORTED_FORMAT = "unsupported_format"
ITEM_CONVERSION_FAILED = "conversion_failed"
PERMANENT_ITEM_ERRORS = (ERROR_REJECTED, ITEM_UNSUPPORTED_FORMAT, ITEM_CONVERSION_FAILED)

class StopRun(Exception):
    def __init__(self, code: str, message: str | None = None):
        super().__init__(message or code)
        self.code = code


@dataclass
class Counters:
    sent: int = 0
    removed: int = 0
    kept: int = 0
    failed: int = 0
    cant_remove: int = 0
    unsupported: int = 0
    converted: int = 0
    unchanged: int = 0
    removed_remotely: int = 0

    def params(self) -> dict[str, int]:
        return dict(self.__dict__)


def item_key(media_item_id: int, target: str, caps: TargetCapabilities) -> str:
    return f"{media_item_id}:{target}" if caps.targets == TARGETS_MANY else str(media_item_id)


def _chunks(items: list, size: int) -> Iterator[list]:
    iterator = iter(items)
    while chunk := list(islice(iterator, size)):
        yield chunk


class TargetRun:
    def __init__(self, session: Session, automation: Automation, column: SyncColumn,
                 adapter: LoadedAdapter, run: RunContext):
        self.session = session
        self.automation = automation
        self.config = column.config
        self.runtime = column.runtime
        self.adapter = adapter
        self.handler: TargetHandler = target_handler(adapter)
        self.run = run
        self.options = self.config.options
        self.caps = self.handler.capabilities(self.options)
        self.counters = Counters()
        self.started = time.monotonic()
        self.scratch = Path(tempfile.mkdtemp(prefix="yaffo-sync-", dir=run.scratch_dir))
        self.client = None
        self.to_send = 0

    # ---- helpers ----------------------------------------------------------

    def ctx(self, pass_kind: str = PASS_INCREMENTAL) -> HookContext:
        return HookContext(options=self.options, state=self.runtime.state, run_id=self.run.job_id,
                           pass_kind=pass_kind, automation_id=self.automation.id, scratch_dir=self.scratch)

    def out_of_time(self) -> bool:
        return time.monotonic() - self.started > self.run.time_budget_seconds or self.run.should_cancel()

    def commit_runtime(self, state_patch: dict | None = None) -> None:
        if state_patch:
            self.runtime = replace(self.runtime, state=merge_state(self.runtime.state, state_patch))
        set_runtime(self.session, self.automation, self.runtime)

    def apply_hook_error(self, envelope: dict, unresolved: list[SyncItem]) -> None:
        """An unreported item may have reached the remote before a response was
        lost. Keep its intent for the next run's reconcile before retrying."""
        for row in unresolved:
            row.status = SYNC_ITEM_PENDING if self.caps.reconcile else SYNC_ITEM_FAILED
            row.error_code = ITEM_NOT_SENT
        error = envelope.get("error")
        self.commit_runtime(envelope.get("state_patch"))
        if error:
            raise StopRun(error["code"], error.get("message"))

    # ---- crash recovery and drift ------------------------------------------

    def recover_and_reconcile(self, rows: list[SyncItem]) -> None:
        if not self.caps.reconcile:
            return
        cursor = self.runtime.cursor(CURSOR_RECONCILE)
        now = self.run.clock()
        last = datetime.fromisoformat(cursor.last_completed_at) if cursor.last_completed_at else None
        drift_due = last is None or now - last >= RECONCILE_EVERY
        stale = [row for row in rows if row.status == SYNC_ITEM_PENDING]
        sent = [row for row in rows if row.status == SYNC_ITEM_SENT] if drift_due else []
        checked = stale + sent
        if not checked:
            return
        found = self.handler.reconcile([self.row_item(row) for row in checked], self.ctx())
        patch = found.pop("__state_patch__", None)
        for row in stale:
            match = found.get(str(row.id))
            if match == PRESENT or isinstance(match, dict) and match.get("status") == PRESENT:
                row.status, row.error_code = SYNC_ITEM_SENT, None
                if isinstance(match, dict) and match.get("remote_id"):
                    row.remote_id = match["remote_id"]
                if isinstance(match, dict) and match.get("details"):
                    row.details = {**(row.details or {}), **match["details"]}
        for row in sent:
            match = found.get(str(row.id))
            if match == MISSING:
                row.status = SYNC_ITEM_REMOVED_REMOTELY
                self.counters.removed_remotely += 1
            elif isinstance(match, dict) and match.get("status") == PRESENT and match.get("details"):
                row.details = {**(row.details or {}), **match["details"]}
        if drift_due:
            self.runtime = self.runtime.with_cursor(CURSOR_RECONCILE, SyncCursor(last_completed_at=now.isoformat()))
        self.commit_runtime(patch)

    def row_item(self, row: SyncItem) -> dict[str, Any]:
        return {"key": str(row.id), "media_item_id": row.media_item_id, "remote_id": row.remote_id,
                "target": row.target, "filename": (row.details or {}).get("filename"),
                "details": row.details or {}}

    # ---- removal -------------------------------------------------------------

    def remove_rows(self, rows: list[SyncItem]) -> None:
        if not rows:
            return
        if not self.caps.remove:
            for row in rows:
                row.status = SYNC_ITEM_CANT_REMOVE
                self.counters.cant_remove += 1
            self.commit_runtime()
            return
        for batch in _chunks(rows, self.caps.max_batch):
            if self.out_of_time():
                return
            for row in batch:
                row.status = SYNC_ITEM_REMOVING
                row.run_id = self.run.job_id
            self.commit_runtime()
            by_key = {str(row.id): row for row in batch}
            envelope = self.handler.remove([self.row_item(row) for row in batch], self.ctx())
            results = self.check(envelope, set(by_key), {"kept"})
            for result in results:
                row = by_key.pop(result["key"])
                error = result.get("error")
                if error is None or error["code"] == ERROR_NOT_FOUND:
                    self.finish_removed(row, kept=bool(result.get("kept")))
                elif error["code"] == ERROR_CHANGED:
                    row.status, row.error_code = SYNC_ITEM_CANT_REMOVE, ERROR_CHANGED
                    self.counters.cant_remove += 1
                else:
                    row.error_code = error["code"]
                    row.attempts += 1
            self.apply_hook_error_for_removal(envelope, list(by_key.values()))

    def apply_hook_error_for_removal(self, envelope: dict, unresolved: list[SyncItem]) -> None:
        # Unattempted removals stay `removing`: the next run retries them.
        error = envelope.get("error")
        self.commit_runtime(envelope.get("state_patch"))
        if error:
            raise StopRun(error["code"], error.get("message"))

    def finish_removed(self, row: SyncItem, kept: bool) -> None:
        if kept:
            self.counters.kept += 1
        else:
            self.counters.removed += 1
        if row.media_item_id is None:
            self.session.delete(row)
            return
        row.status, row.error_code, row.remote_id = SYNC_ITEM_REMOVED, None, None
        row.details = None

    def check(self, envelope: Any, keys: set[str], extra_fields: set[str]) -> list[dict]:
        allowed = extra_fields | {"details"}
        return check_or_fatal(envelope, keys, allowed)

    # ---- pushing ---------------------------------------------------------------

    def prepare(self, row: SyncItem, desired: Desired, media: MediaItem) -> dict[str, Any] | None:
        """The batch item for a row, converting the file when the target needs it.
        Returns None (and records the failure on the row) when it can't be sent."""
        source = Path(media.full_file_path)
        if not source.is_file():
            row.status, row.error_code = SYNC_ITEM_FAILED, ITEM_SOURCE_MISSING
            self.counters.failed += 1
            return None
        path, filename = source, source.name
        sent_as = None
        try:
            if needs_conversion(source, self.caps.accepts):
                path = convert_to_jpeg(source, self.scratch / f"{row.id}.jpg", self.caps.accepts)
                filename = f"{source.stem}.jpg"
                sent_as = {"type": "image/jpeg", "size": path.stat().st_size}
                self.counters.converted += 1
        except UnsupportedFormat:
            row.status, row.error_code = SYNC_ITEM_FAILED, ITEM_UNSUPPORTED_FORMAT
            self.counters.unsupported += 1
            return None
        except Exception as e:  # noqa: BLE001 - a photo Pillow can't decode is one failed item
            logger.warning(f"sync '{self.automation.slug}': could not convert {source}: {e}")
            row.status, row.error_code = SYNC_ITEM_FAILED, ITEM_CONVERSION_FAILED
            self.counters.failed += 1
            return None
        if not self.adapter.is_builtin:
            # Cloud APIs have no idempotency key for a completed upload whose
            # response was lost. A stable name lets the next run find that exact
            # item, even when two library photos share a filename.
            name = Path(filename)
            filename = f"{name.stem}__yaffo_{row.id}{name.suffix}"
        item = {
            "key": str(row.id), "media_item_id": media.id, "target": desired.target,
            "caption": desired.caption, "filename": filename, "mime": mime_for(path),
            "size": path.stat().st_size, "taken_at": {"local": media.date_taken} if media.date_taken else {},
        }
        if self.adapter.is_builtin:
            item["path"] = str(path)
        if sent_as:
            item["sent_as"] = sent_as
        return item

    def push_rows(self, work: list[tuple[SyncItem, Desired]]) -> None:
        media_by_id = {m.id: m for m in self.session.query(MediaItem)
                       .filter(MediaItem.id.in_([d.media_item_id for _, d in work])).all()} if work else {}
        pending: list[tuple[SyncItem, dict]] = []
        for row, desired in work:
            media = media_by_id.get(desired.media_item_id)
            if media is None:
                continue
            item = self.prepare(row, desired, media)
            if item is not None:
                pending.append((row, item))
        self.commit_runtime()
        self.to_send = len(pending)
        self.report_progress()
        for batch in self.batches(pending):
            if self.out_of_time():
                for row, _ in batch:
                    if row.status == SYNC_ITEM_PENDING and row.remote_id is None:
                        row.status, row.error_code = SYNC_ITEM_FAILED, ITEM_NOT_SENT
                self.commit_runtime()
                return
            self.push_batch(batch)

    def batches(self, pending: list[tuple[SyncItem, dict]]) -> Iterator[list[tuple[SyncItem, dict]]]:
        batch: list[tuple[SyncItem, dict]] = []
        size = 0
        for row, item in pending:
            if batch and (len(batch) >= self.caps.max_batch or size + item["size"] > self.caps.max_batch_bytes):
                yield batch
                batch, size = [], 0
            batch.append((row, item))
            size += item["size"]
        if batch:
            yield batch

    def push_batch(self, batch: list[tuple[SyncItem, dict]]) -> None:
        for row, _ in batch:
            row.status, row.error_code = SYNC_ITEM_PENDING, None
            row.attempts += 1
            row.run_id = self.run.job_id
        for row, item in batch:
            row.details = {**(row.details or {}), "filename": item["filename"]}
        self.commit_runtime()
        by_key = {item["key"]: (row, item) for row, item in batch}
        hook_batch = [{k: v for k, v in item.items() if k != "sent_as"} for _, item in batch]
        if self.client is not None:
            self.client.upload_paths = {item["media_item_id"]: (
                self.scratch / f"{row.id}.jpg" if item.get("sent_as") else
                Path(self.session.get(MediaItem, item["media_item_id"]).full_file_path))
                for row, item in batch}
        try:
            envelope = self.handler.push(hook_batch, self.ctx())
        finally:
            if self.client is not None:
                self.client.upload_paths = {}
        results = self.check(envelope, set(by_key), {"remote_id"})
        for result in results:
            row, item = by_key.pop(result["key"])
            error = result.get("error")
            if error is not None:
                row.status, row.error_code = SYNC_ITEM_FAILED, error["code"]
                self.counters.failed += 1
                continue
            if not isinstance(result.get("remote_id"), str):
                raise_fatal(f"result for item {result['key']} has no remote_id")
            row.status, row.remote_id = SYNC_ITEM_SENT, result["remote_id"]
            if result.get("details") is not None and not isinstance(result["details"], dict):
                raise_fatal(f"result for item {result['key']} has invalid details")
            details = dict(result.get("details") or {})
            if item.get("sent_as"):
                details["sent_as"] = item["sent_as"]
            row.details = details or None
            self.counters.sent += 1
        self.apply_hook_error(envelope, [row for row, _ in by_key.values()])
        self.report_progress()
        logger.info(f"sync '{self.automation.slug}' sent {self.counters.sent} and failed "
                    f"{self.counters.failed} of {self.to_send}")

    def report_progress(self) -> None:
        if self.run.on_progress:
            self.run.on_progress(self.counters.sent + self.counters.failed, self.to_send)

    # ---- the run -----------------------------------------------------------------

    def execute(self) -> RunResult:
        self.client = access.attach(self.session, self.adapter, self.config, vouched=self.runtime.followups,
                                    should_cancel=self.run.should_cancel)
        problem = self.handler.destination_problem(self.options)
        if problem:
            return pause(self.session, self.automation, self.runtime, problem)
        if self.adapter.is_builtin and "folder" in self.options:
            guard = folder_target_problem(self.session, Path(self.options["folder"]))
            if guard:
                return RunResult(outcome=codes.OUTCOME_SYNC_NOT_RUN, problem=codes.PROBLEM_SYNC_INVALID_CONFIG,
                                 error=f"destination folder problem: {guard}")
            sweep_temps(Path(self.options["folder"]), str(self.automation.id))
        try:
            desired = resolve_selection(self.session, self.config.selection, self.automation.name,
                                        self.automation.published_code)
            if self.adapter.provider == "onedrive":
                desired = one_drive_targets(self.session, desired, self.config.selection,
                                            self.options.get("layout", LAYOUT_PER_TARGET))
        except SelectionMissing:
            shutil.rmtree(self.scratch, ignore_errors=True)
            return pause(self.session, self.automation, self.runtime, PAUSE_SELECTION_MISSING)
        except ValueError as e:
            shutil.rmtree(self.scratch, ignore_errors=True)
            return self.result(problem=codes.PROBLEM_SYNC_INVALID_CONFIG, error=str(e), failed=True)

        rows = sync_repository.rows(self.session, self.automation.id)
        try:
            self.recover_and_reconcile(rows)
            wanted = {item_key(d.media_item_id, d.target, self.caps): d for d in desired}
            by_key = {row.item_key: row for row in rows}

            to_remove = [row for row in rows if row.status == SYNC_ITEM_REMOVING
                         or (row.status in (SYNC_ITEM_SENT, SYNC_ITEM_PENDING) and row.item_key not in wanted
                             and row.remote_id is not None)]
            dropped = [row for row in rows if row.item_key not in wanted
                       and row.status in (SYNC_ITEM_FAILED, SYNC_ITEM_PENDING) and row.remote_id is None]
            for row in dropped:
                self.session.delete(row)
            self.remove_rows(to_remove)

            work: list[tuple[SyncItem, Desired]] = []
            for key, desired_item in wanted.items():
                row = by_key.get(key)
                if row is None:
                    row = SyncItem(automation_id=self.automation.id, item_key=key,
                                   media_item_id=desired_item.media_item_id, target=desired_item.target,
                                   status=SYNC_ITEM_PENDING, attempts=0)
                    self.session.add(row)
                    work.append((row, desired_item))
                elif row.status in (SYNC_ITEM_SENT, SYNC_ITEM_REMOVED_REMOTELY, SYNC_ITEM_CANT_REMOVE,
                                    SYNC_ITEM_REMOVING):
                    self.counters.unchanged += row.status == SYNC_ITEM_SENT
                elif row.status == SYNC_ITEM_FAILED and row.error_code in PERMANENT_ITEM_ERRORS:
                    continue
                else:
                    row.target = desired_item.target
                    work.append((row, desired_item))
            self.session.flush()
            self.push_rows(work)
        except HookFailed as e:
            self.session.rollback()
            if self.client is not None and self.client.auth_failed:
                if self.config.connection_key:
                    connections.mark_needs_sign_in(self.session.get_bind(), self.config.connection_key)
                return stop_sync_for_sign_in(self.session, self.automation, self.runtime, str(e))
            return self.result(problem=codes.PROBLEM_SYNC_STOPPED, problem_params={"code": ERROR_FATAL},
                               error=str(e), failed=True)
        except StopRun as stop:
            self.commit_runtime()
            if stop.code == ERROR_AUTH or self.client is not None and self.client.auth_failed:
                if self.config.connection_key:
                    connections.mark_needs_sign_in(self.session.get_bind(), self.config.connection_key)
                result = stop_sync_for_sign_in(self.session, self.automation, self.runtime, str(stop))
                result.params = self.counters.params()
                return result
            if stop.code in (ERROR_RATE_LIMITED, ERROR_RETRY_LATER, ERROR_NOT_FOUND):
                schedule_continuation(self.session, self.automation, 60)
            return self.result(problem=codes.PROBLEM_SYNC_STOPPED, problem_params={"code": stop.code},
                               error=str(stop), failed=stop.code in (ERROR_FATAL, ERROR_AUTH))
        finally:
            shutil.rmtree(self.scratch, ignore_errors=True)
        failed = self.counters.failed + self.counters.unsupported
        if failed:
            return self.result(problem=codes.PROBLEM_SYNC_ITEMS_FAILED, problem_params={"count": failed})
        return self.result()

    def result(self, problem: str | None = None, problem_params: dict | None = None,
               error: str | None = None, failed: bool = False) -> RunResult:
        params = {**self.counters.params(), "folder": int(self.adapter.handler_key == SYNC_HANDLER_FOLDER_TARGET)}
        return RunResult(outcome=codes.OUTCOME_SYNC_SENT, params=params, problem=problem,
                         problem_params=problem_params or {}, error=error, failed_run=failed)


def raise_fatal(message: str) -> None:
    raise StopRun(ERROR_FATAL, message)


def stop_sync_for_sign_in(session: Session, automation: Automation, runtime, message: str) -> RunResult:
    result = stop(session, automation, runtime, RUNTIME_NEEDS_SIGN_IN,
                  codes.PROBLEM_SYNC_NEEDS_SIGN_IN, error=message)
    result.outcome = codes.OUTCOME_SYNC_SENT
    result.problem_params = {"reason": access.ACCOUNT_NEEDS_SIGN_IN}
    return result


def check_or_fatal(envelope: Any, keys: set[str], allowed: set[str]) -> list[dict]:
    try:
        return check_results(envelope, keys, allowed)
    except HookOutputError as e:
        raise StopRun(ERROR_FATAL, f"the service's code returned something invalid: {e}") from e


def run_target(session: Session, automation: Automation, column: SyncColumn, adapter: LoadedAdapter,
               run: RunContext) -> RunResult:
    return TargetRun(session, automation, column, adapter, run).execute()
