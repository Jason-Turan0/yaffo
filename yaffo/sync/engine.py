"""The host sync engine: one run of one sync automation.

`run_sync` loads and validates the sync, checks consent, and hands off to the target
or source run (target_run.py, source_run.py). It returns a RunResult (codes, never
sentences) that the run task records on the run's Job. The engine owns
`automations.sync["runtime"]` and the `sync_items` ledger; it never writes the config.

See docs/development/automation-sync-adapters.md (Host engine).
"""
from __future__ import annotations

from dataclasses import replace

from sqlalchemy.orm import Session

from yaffo.db.models import Automation, SYNC_ROLE_SOURCE
from yaffo.db.repositories import sync_repository
from yaffo.logging_config import get_logger
from yaffo.sync.access import account_problem
from yaffo.sync.adapters import AdapterUnavailable, load_adapter
from yaffo.sync.consent import has_consent
from yaffo.sync.run_common import RunContext, RunResult, set_runtime, stop
from yaffo.sync.schemas import (
    RUNTIME_INVALID_CONFIG, RUNTIME_NEEDS_CONSENT, RUNTIME_NEEDS_SIGN_IN, RUNTIME_READY, SyncColumn,
    SyncSchemaError,
)
from yaffo.sync.source_run import run_source
from yaffo.sync.target_run import run_target
from yaffo.utils import job_codes as codes

logger = get_logger(__name__, 'background_tasks')


def run_sync(session: Session, automation_id: int, run: RunContext) -> RunResult:
    automation = session.get(Automation, automation_id)
    if automation is None or automation.sync is None:
        return RunResult(outcome=codes.OUTCOME_SYNC_NOT_RUN, problem=codes.PROBLEM_SYNC_INVALID_CONFIG,
                         error="not a sync automation")
    try:
        column = sync_repository.load_sync(automation)
    except SyncSchemaError as e:
        logger.warning(f"sync '{automation.slug}' has invalid stored JSON: {e}")
        return RunResult(outcome=codes.OUTCOME_SYNC_NOT_RUN, problem=codes.PROBLEM_SYNC_INVALID_CONFIG,
                         error=str(e))
    runtime = column.runtime
    try:
        adapter = load_adapter(session, column.config.adapter_id)
    except AdapterUnavailable as e:
        return stop(session, automation, runtime, RUNTIME_INVALID_CONFIG,
                     codes.PROBLEM_SYNC_INVALID_CONFIG, error=str(e))
    if not has_consent(column.config, adapter):
        return stop(session, automation, runtime, RUNTIME_NEEDS_CONSENT, codes.PROBLEM_SYNC_NEEDS_CONSENT)
    account = account_problem(session, adapter, column.config)
    if account:
        result = stop(session, automation, runtime, RUNTIME_NEEDS_SIGN_IN, codes.PROBLEM_SYNC_NEEDS_SIGN_IN)
        result.problem_params = {"reason": account}
        return result

    if runtime.status != RUNTIME_READY:
        runtime = replace(runtime, status=RUNTIME_READY, pause_code=None)
    runtime = replace(runtime, last_run_at=run.clock().isoformat())
    set_runtime(session, automation, runtime)
    column = SyncColumn(config=column.config, runtime=runtime)
    if column.config.role == SYNC_ROLE_SOURCE:
        return run_source(session, automation, column, adapter, run)
    return run_target(session, automation, column, adapter, run)
