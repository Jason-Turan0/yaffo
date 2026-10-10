"""What the source and target runs share: the run's injected dependencies, its
result, hook-output validation, and the runtime helpers for stopping or pausing."""
from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable

from sqlalchemy.orm import Session

from yaffo.background_tasks.automation_runs import RunOutcome
from yaffo.db.models import Automation
from yaffo.db.repositories import sync_repository
from yaffo.logging_config import get_logger
from yaffo.sync.handlers import ERROR_CODES, ERROR_CHANGED
from yaffo.sync.schemas import RUNTIME_PAUSED, STATE_MAX_BYTES, SyncRuntime
from yaffo.utils import job_codes as codes
from yaffo.utils.time import utcnow

logger = get_logger(__name__, 'background_tasks')

# A run stops at a page/batch boundary after this long; the next run resumes.
DEFAULT_TIME_BUDGET_SECONDS = 15 * 60


class HookOutputError(Exception):
    """A handler returned something that doesn't fit the contract: treated as
    `fatal`, never half-applied."""


@dataclass
class RunContext:
    """What a run needs besides the session: injected so tests can run the engine
    without the task queue, the indexer, or the clock."""
    job_id: str | None = None
    time_budget_seconds: float = DEFAULT_TIME_BUDGET_SECONDS
    index_files: Callable[[Session, list[str], int], None] | None = None
    reindex_items: Callable[[Session, list[int]], None] | None = None
    clock: Callable[[], Any] = utcnow
    scratch_dir: Path | None = None
    on_progress: Callable[[int, int], None] | None = None
    should_cancel: Callable[[], bool] = lambda: False


@dataclass
class RunResult:
    outcome: str
    params: dict[str, int] = field(default_factory=dict)
    problem: str | None = None
    problem_params: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    failed_run: bool = False

    def to_outcome(self) -> RunOutcome:
        return RunOutcome(self.outcome, dict(self.params))

    def job_data(self) -> str:
        data = self.to_outcome().job_data()
        if self.problem:
            data = codes.with_problem(data, self.problem, **self.problem_params)
        return data


def merge_state(state: dict, patch: dict | None) -> dict:
    """Apply a hook's state_patch: None deletes a key. Capped at STATE_MAX_BYTES."""
    if not patch:
        return state
    if not isinstance(patch, dict):
        raise HookOutputError("state_patch must be an object")
    merged = dict(state)
    for key, value in patch.items():
        if value is None:
            merged.pop(key, None)
        else:
            merged[str(key)] = value
    if len(json.dumps(merged)) > STATE_MAX_BYTES:
        raise HookOutputError("state_patch would make the sync's state larger than 64 KiB")
    return merged


def check_error(error: Any) -> dict | None:
    if error is None:
        return None
    if not isinstance(error, dict) or error.get("code") not in (*ERROR_CODES, ERROR_CHANGED):
        raise HookOutputError(f"invalid error {error!r}")
    return error


def check_results(envelope: Any, keys: set[str], allowed_fields: set[str]) -> list[dict]:
    """The `results` of a target hook, checked: each names a key from its batch at
    most once and carries only known fields."""
    if not isinstance(envelope, dict):
        raise HookOutputError("hook must return an object")
    unknown = set(envelope) - {"results", "state_patch", "error"}
    if unknown:
        raise HookOutputError(f"unknown keys {sorted(unknown)}")
    results = envelope.get("results") or []
    if not isinstance(results, list):
        raise HookOutputError("results must be a list")
    seen: set[str] = set()
    for result in results:
        if not isinstance(result, dict) or result.get("key") not in keys:
            raise HookOutputError(f"result {result!r} doesn't name an item of its batch")
        if result["key"] in seen:
            raise HookOutputError(f"two results for item {result['key']}")
        seen.add(result["key"])
        extra = set(result) - allowed_fields - {"key", "error"}
        if extra:
            raise HookOutputError(f"unknown result fields {sorted(extra)}")
        check_error(result.get("error"))
    check_error(envelope.get("error"))
    return results


def set_runtime(session: Session, automation: Automation, runtime: SyncRuntime) -> None:
    sync_repository.save_runtime(automation, runtime)
    session.commit()


def stop(session: Session, automation: Automation, runtime: SyncRuntime, status: str,
          problem: str, pause_code: str | None = None, error: str | None = None) -> RunResult:
    set_runtime(session, automation, replace(runtime, status=status, pause_code=pause_code))
    params = {"pause_code": pause_code} if pause_code else {}
    return RunResult(outcome=codes.OUTCOME_SYNC_NOT_RUN, problem=problem, problem_params=params, error=error)


def pause(session: Session, automation: Automation, runtime: SyncRuntime, pause_code: str) -> RunResult:
    return stop(session, automation, runtime, RUNTIME_PAUSED, codes.PROBLEM_SYNC_PAUSED, pause_code=pause_code)
