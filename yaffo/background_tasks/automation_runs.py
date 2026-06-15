import json
import uuid
from datetime import datetime

from sqlalchemy.orm import Session

from yaffo.background_tasks.automation_sandbox.executor import run_automation
from yaffo.background_tasks.events import EventContext
from yaffo.db.models import (
    Automation,
    Job,
    JOB_STATUS_COMPLETED,
    JOB_STATUS_FAILED,
    JOB_STATUS_RUNNING,
)
from yaffo.logging_config import get_logger

logger = get_logger(__name__, 'background_tasks')


def run_and_record(session: Session, automation: Automation, context: EventContext | None) -> Job:
    """Run a custom automation's code and record it as a Job (the run history).

    Opens a RUNNING Job tagged with `automation_id`, runs the sandboxed code, then
    finalises the Job to COMPLETED/FAILED with the captured print output (and the
    error on failure). The sandbox returns failures as data, so a bad script
    becomes a FAILED Job, not an exception. These Jobs are never handed to
    complete_job_task, so they emit no events (and can't feed a trigger loop)."""
    job = Job(
        id=str(uuid.uuid4()),
        name=automation.slug,
        status=JOB_STATUS_RUNNING,
        automation_id=automation.id,
        message=automation.name,
        task_count=1,
        started_at=datetime.utcnow(),
    )
    session.add(job)
    session.commit()

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
