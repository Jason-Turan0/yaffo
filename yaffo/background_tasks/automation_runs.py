import json
import uuid
from typing import Callable
from sqlalchemy.orm import Session
from yaffo.utils.time import utcnow
from yaffo.background_tasks.automation_sandbox.executor import run_automation
from yaffo.background_tasks.events import EventContext, event_chain_scope
from yaffo.background_tasks.progress_reporter import ProgressReporter
from yaffo.background_tasks.schedule_scope import ScheduleScopeError
from yaffo.db.models import (
    Automation,
    Job,
    JOB_STATUS_CANCELLED,
    JOB_STATUS_COMPLETED,
    JOB_STATUS_FAILED,
)
from yaffo.db.repositories.job_repository import is_job_cancelled, open_run_job
from yaffo.logging_config import get_logger

logger = get_logger(__name__, 'background_tasks')


def _open_run_job(session: Session, automation: Automation, job_id: str | None) -> Job | None:
    """Open (or, on a queue retry, adopt) the RUNNING Job tagged with the automation
    id -- the run row. `job_id` is the task's queue id (job_repository.run_job_id).
    None when that Job already ended, so the run must not happen again."""
    return open_run_job(session, job_id or str(uuid.uuid4()),
                        name=automation.slug, automation_id=automation.id, message=automation.name)


def record_dispatch_failure(session: Session, automation: Automation, trigger_id: int,
                            scheduled_for, error: Exception, *, trigger_type: str = "schedule") -> Job:
    """Record a trigger that could not enqueue a run as a failed Job.

    The dispatcher commits the Job. Schedules also advance to the next cron slot,
    so a persistent scope error produces one visible failure per occurrence.
    """
    now = utcnow()
    job = Job(
        id=str(uuid.uuid4()), name=automation.slug, message=automation.name,
        automation_id=automation.id, status=JOB_STATUS_FAILED,
        task_count=1, completed_count=0, error_count=1, cancelled_count=0,
        started_at=now, completed_at=now, error=str(error),
        job_data=json.dumps({
            "trigger_id": trigger_id,
            "scheduled_for": scheduled_for.isoformat() if scheduled_for else None,
            "dispatch_error_code": (
                "invalid_scope" if isinstance(error, ScheduleScopeError) else
                "event_dispatch_failed" if trigger_type == "event" else "dispatch_failed"
            ),
        }),
    )
    session.add(job)
    return job


def record_run(session: Session, automation: Automation, work: Callable[[ProgressReporter], str],
               media_item_ids: list[int] | None = None, job_id: str | None = None) -> Job | None:
    """Record one run of a *system* automation as a Job (the run history).

    Opens a RUNNING Job tagged with `automation_id`, runs `work` (which performs the
    automation's side effects and returns a one-line summary), then finalises the Job
    to COMPLETED with that summary in job_data, or to FAILED with the exception
    message. A run cancelled while `work` ran stays CANCELLED (the reporter's
    run_with_progress stops at its next check). `work`'s exception is captured (logged, not re-raised) so a failing run
    becomes a FAILED Job rather than a crashed worker. Like run_and_record these Jobs
    never go through complete_job_task, so they emit no events (and can't feed a
    trigger loop). `job_id` is the task's queue id, so a retried task records on
    the same Job; returns None (without running `work`) when that Job already ended."""
    job = _open_run_job(session, automation, job_id)
    if job is None:
        logger.info(f"automation '{automation.slug}' run {job_id} already ended; not running it again")
        return None
    try:
        summary = ("No indexed media items to process" if media_item_ids is not None and not media_item_ids
                   else work(ProgressReporter(session, job.id)))
    except Exception as e:
        session.rollback()
        job = session.get(Job, job.id)
        if not is_job_cancelled(session, job.id):
            job.status = JOB_STATUS_FAILED
        job.error = str(e)
        job.completed_at = utcnow()
        session.commit()
        logger.error(f"automation '{automation.slug}' run {job.id} failed: {e}", exc_info=True)
        return job

    if not is_job_cancelled(session, job.id):
        job.status = JOB_STATUS_COMPLETED
    job.completed_at = utcnow()
    job.job_data = json.dumps({"output": summary or ""})
    session.commit()
    logger.debug(f"automation '{automation.slug}' run recorded as job {job.id} ({job.status})")
    return job


def run_and_record(session: Session, automation: Automation, context: EventContext | None,
                   job_id: str | None = None) -> Job | None:
    """Run a custom automation's code and record it as a Job (the run history).

    Opens a RUNNING Job tagged with `automation_id`, runs the sandboxed code, then
    finalises the Job to COMPLETED/FAILED with the captured print output (and the
    error on failure). The sandbox returns failures as data, so a bad script
    becomes a FAILED Job, not an exception. These Jobs are never handed to
    complete_job_task, so they emit no events (and can't feed a trigger loop).
    `job_id` works as in record_run."""
    job = _open_run_job(session, automation, job_id)
    if job is None:
        logger.info(f"automation '{automation.slug}' run {job_id} already ended; not running it again")
        return None

    # Establish the causal chain so any event the script emits — including ones fired
    # from inside a mutating host action — carries this automation, letting the loop
    # guard break a cycle that would re-trigger it.
    origin = context.origin_automation_ids if context else []
    # The reporter binds this run's Job + session; it's handed to the sandbox so the
    # script's report_progress host action updates *this* run (no global state).
    reporter = ProgressReporter(session, job.id)
    with event_chain_scope(origin, automation.id):
        result = run_automation(session, automation, context, progress=reporter)

    job.completed_at = utcnow()
    job.job_data = json.dumps({"output": result.output})
    # A script that called report_progress already set task_count/completed_count —
    # keep those. Only stamp a single unit of work for a run that reported none, so
    # the history still shows the run happened.
    reported_progress = bool(job.task_count)
    if result.cancelled or is_job_cancelled(session, job.id):
        job.status = JOB_STATUS_CANCELLED
    elif result.success:
        job.status = JOB_STATUS_COMPLETED
        if not reported_progress:
            job.completed_count = 1
    else:
        job.status = JOB_STATUS_FAILED
        if not reported_progress:
            job.error_count = 1
        job.error = result.error
    session.commit()

    logger.debug(f"automation '{automation.slug}' run recorded as job {job.id} ({job.status})")
    return job
