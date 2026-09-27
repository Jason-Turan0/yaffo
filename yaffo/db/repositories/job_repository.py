"""Job-status reads and progress bookkeeping shared by the background tasks.

Cancellation is a Job.status flip committed by another process (the Cancel
button, the assistant's cancel_job action). A task's own session can't be
trusted to see it: its identity map holds a stale Job, and an open transaction
reads a stale snapshot. So the check reads through a short-lived session of its
own on the same engine.

Every progress tick also refreshes `estimated_completed_at`: the rate so far
(items finished since `started_at`) projected over the items still to go.

A task that records its run on a Job it creates itself (automation runs, file
sync, duplicate scans) keys that Job by its queue task id (`open_run_job`). The
queue re-runs a task stranded by a crash with the same id, so the retry adopts
the Job instead of leaving it RUNNING forever and opening a second one.

`completed_at` records when a job's work actually ended, however it ended. A
cancel only flips the status, so a CANCELLED job without `completed_at` is still
stopping: its task hasn't reached its next cancellation check yet.
"""
import uuid
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import case, select, update
from sqlalchemy.orm import Session

from yaffo.db.models import Job, JOB_STATUS_CANCELLED, JOB_STATUS_COMPLETED, JOB_STATUS_FAILED, JOB_STATUS_RUNNING
from yaffo.utils.job_codes import with_problem
from yaffo.utils.time import utcnow


def is_job_cancelled(session: Session, job_id: str) -> bool:
    """Whether Job `job_id` has been cancelled (or deleted) by someone else."""
    with Session(session.get_bind()) as fresh:
        status = fresh.scalar(select(Job.status).where(Job.id == job_id))
    return status is None or status == JOB_STATUS_CANCELLED


def run_job_id(task: Any) -> str:
    """The id for the Job a task records its run on: its queue task id (from
    `context=True`), stable across the queue's crash retries. A fresh id when the
    task is called without one (tests calling `.fn` directly)."""
    return task.id if task is not None else str(uuid.uuid4())


def open_run_job(session: Session, job_id: str, **fields: Any) -> Job | None:
    """Get or create Job `job_id` and start it RUNNING, committed.

    `fields` are the new Job's columns (name, automation_id, message, task_count...).
    When the Job already exists this is the queue retrying a stranded task: the Job
    restarts with fresh counts and start time (the work runs again from the top).
    Returns None when the Job already reached an end -- an earlier attempt finished
    it, or it was cancelled -- so the caller doesn't run the work again; a
    cancelled one gets completed_at, since nothing else will stop it."""
    now = utcnow()
    counts = dict(task_count=fields.pop("task_count", 0), completed_count=0, error_count=0, cancelled_count=0)
    job = session.get(Job, job_id)
    if job is None:
        job = Job(id=job_id, status=JOB_STATUS_RUNNING, started_at=now, created_at=now, **counts, **fields)
        session.add(job)
    elif job.status == JOB_STATUS_CANCELLED:
        mark_job_stopped(session, job_id)
        session.commit()
        return None
    elif job.status in (JOB_STATUS_COMPLETED, JOB_STATUS_FAILED):
        return None
    else:
        for key, value in counts.items():
            setattr(job, key, value)
        job.status, job.started_at = JOB_STATUS_RUNNING, now
        job.completed_at = job.estimated_completed_at = job.error = None
    session.commit()
    return job


def fail_run_job(session: Session, job_id: str, error: str, problem: str) -> bool:
    """Finish the Job keyed by a task's queue id when that task ended in error
    without finishing it -- its worker crashed, or it raised outside its own
    handling. FAILED with the English `error` and the `problem` code the UI
    translates (utils/job_codes.py); a cancelled Job just gets completed_at (its
    work has stopped). Returns whether a Job was changed: False when there's no such Job
    (most tasks' ids aren't Job ids) or it had already ended. Commits."""
    job = session.get(Job, job_id)
    if job is None or job.status in (JOB_STATUS_COMPLETED, JOB_STATUS_FAILED):
        return False
    if job.status == JOB_STATUS_CANCELLED:
        if job.completed_at is not None:
            return False
    else:
        job.status, job.error = JOB_STATUS_FAILED, error
        job.job_data = with_problem(job.job_data, problem)
        job.estimated_completed_at = None
    job.completed_at = job.completed_at or utcnow()
    session.commit()
    return True


def finished_status():
    """An UPDATE value for Job.status when a task's work ends: COMPLETED, unless the
    job was cancelled meanwhile. Decided in the UPDATE itself, so a cancel landing
    after the task's last check isn't overwritten."""
    return case((Job.status == JOB_STATUS_CANCELLED, JOB_STATUS_CANCELLED), else_=JOB_STATUS_COMPLETED)


def mark_job_stopped(session: Session, job_id: str) -> None:
    """Record that Job `job_id`'s work has ended (completed_at), leaving its status
    alone. For a cancelled job this turns Stopping into Cancelled. The caller commits."""
    session.execute(
        update(Job).where(Job.id == job_id, Job.completed_at.is_(None)).values(completed_at=utcnow())
        .execution_options(synchronize_session=False)
    )


def earliest_started_at(started: datetime):
    """An UPDATE value for Job.started_at: `started` unless the row already has an
    earlier start. Lets each batch of a chord stamp its own start time in its
    progress write, so the job's start is the first batch's, with no extra write."""
    return case(
        (Job.started_at.is_(None), started),
        (Job.started_at > started, started),
        else_=Job.started_at,
    )


def estimate_completion(started_at: datetime | None, task_count: int | None, completed: int | None,
                        errors: int | None, cancelled: int | None, now: datetime) -> datetime | None:
    """When the job should finish at its rate so far, or None before there's a rate.
    Cancelled items took no time, so they count as neither work done nor work left."""
    done = (completed or 0) + (errors or 0)
    if started_at is None or not task_count or done <= 0:
        return None
    remaining = task_count - done - (cancelled or 0)
    if remaining <= 0:
        return now
    elapsed = max(now - started_at, timedelta(0))
    return now + elapsed * (remaining / done)


def refresh_estimated_completion(session: Session, job_id: str) -> None:
    """Recompute Job `job_id`'s estimated_completed_at from its current counts.

    Call in a progress tick's transaction, after the counts are written (bulk
    UPDATEs with `Job.x + n` included): it reads the row back through the same
    transaction, which holds SQLite's write lock, so concurrent batches can't
    interleave between the read and the write. The caller commits."""
    row = session.execute(
        select(Job.started_at, Job.task_count, Job.completed_count, Job.error_count, Job.cancelled_count)
        .where(Job.id == job_id)
    ).one_or_none()
    if row is None:
        return
    eta = estimate_completion(*row, now=utcnow())
    session.execute(
        update(Job).where(Job.id == job_id).values(estimated_completed_at=eta)
        .execution_options(synchronize_session=False)
    )
