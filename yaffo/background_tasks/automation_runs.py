import json
import uuid
from datetime import datetime
from typing import Callable
from sqlalchemy.orm import Session
from yaffo.background_tasks.automation_sandbox.executor import run_automation
from yaffo.background_tasks.events import EventContext, event_chain_scope
from yaffo.background_tasks.progress_reporter import ProgressReporter
from yaffo.db.models import (
    Automation,
    Job,
    JOB_STATUS_COMPLETED,
    JOB_STATUS_FAILED,
    JOB_STATUS_RUNNING,
)
from yaffo.logging_config import get_logger

logger = get_logger(__name__, 'background_tasks')


def _open_run_job(session: Session, automation: Automation) -> Job:
    """Open and commit a RUNNING Job tagged with the automation id (the run row)."""
    job = Job(
        id=str(uuid.uuid4()),
        name=automation.slug,
        status=JOB_STATUS_RUNNING,
        automation_id=automation.id,
        message=automation.name,
        started_at=datetime.utcnow(),
    )
    session.add(job)
    session.commit()
    return job


def record_run(session: Session, automation: Automation, work: Callable[[ProgressReporter], str]) -> Job:
    """Record one run of a *system* automation as a Job (the run history).

    Opens a RUNNING Job tagged with `automation_id`, runs `work` (which performs the
    automation's side effects and returns a one-line summary), then finalises the Job
    to COMPLETED with that summary in job_data, or to FAILED with the exception
    message. `work`'s exception is captured (logged, not re-raised) so a failing run
    becomes a FAILED Job rather than a crashed worker. Like run_and_record these Jobs
    never go through complete_job_task, so they emit no events (and can't feed a
    trigger loop)."""
    job = _open_run_job(session, automation)
    try:
        summary = work(ProgressReporter(session, job.id))
    except Exception as e:
        session.rollback()
        job = session.get(Job, job.id)
        job.status = JOB_STATUS_FAILED
        job.error = str(e)
        job.completed_at = datetime.utcnow()
        session.commit()
        logger.error(f"automation '{automation.slug}' run {job.id} failed: {e}", exc_info=True)
        return job

    job.status = JOB_STATUS_COMPLETED
    job.completed_at = datetime.utcnow()
    job.job_data = json.dumps({"output": summary or ""})
    session.commit()
    logger.info(f"automation '{automation.slug}' run recorded as job {job.id} ({job.status})")
    return job


def run_and_record(session: Session, automation: Automation, context: EventContext | None) -> Job:
    """Run a custom automation's code and record it as a Job (the run history).

    Opens a RUNNING Job tagged with `automation_id`, runs the sandboxed code, then
    finalises the Job to COMPLETED/FAILED with the captured print output (and the
    error on failure). The sandbox returns failures as data, so a bad script
    becomes a FAILED Job, not an exception. These Jobs are never handed to
    complete_job_task, so they emit no events (and can't feed a trigger loop)."""
    job = _open_run_job(session, automation)

    # Establish the causal chain so any event the script emits — including ones fired
    # from inside a mutating host action — carries this automation, letting the loop
    # guard break a cycle that would re-trigger it.
    origin = context.origin_automation_ids if context else []
    with event_chain_scope(origin, automation.id):
        result = run_automation(session, automation, context)

    job.completed_at = datetime.utcnow()
    job.job_data = json.dumps({"output": result.output})
    if result.success:
        job.status = JOB_STATUS_COMPLETED
        job.completed_count = 1
    else:
        job.status = JOB_STATUS_FAILED
        job.error_count = 1
        job.error = result.error
    session.commit()

    logger.info(f"automation '{automation.slug}' run recorded as job {job.id} ({job.status})")
    return job
