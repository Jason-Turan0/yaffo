"""Run history rows: the compact list of past Jobs shown on an automation's detail
page and under the Index Photos job cards. Built from a Job so the template stays
dumb and the per-run-kind display logic lives in one tested place."""
from dataclasses import dataclass
from datetime import datetime

from flask_babel import gettext, ngettext

from yaffo.db.models import (
    Job,
    JOB_STATUS_CANCELLED,
    JOB_STATUS_COMPLETED,
    JOB_STATUS_FAILED,
    JOB_STATUS_PENDING,
    JOB_STATUS_RUNNING,
)

RUN_FINISHED_STATUSES = (JOB_STATUS_COMPLETED, JOB_STATUS_FAILED, JOB_STATUS_CANCELLED)


@dataclass(frozen=True)
class RunView:
    """A single row of a run history."""
    job_id: str
    status: str
    status_label: str
    status_chip: str       # chip tone modifier for the status badge
    is_finished: bool
    is_error: bool
    progress: int          # 0–100; shown for in-progress runs
    started_at: datetime | None
    finished_at: datetime | None
    label: str             # what ran ("Index photos"), for lists that mix kinds
    summary: str
    error: str | None
    automation_slug: str | None  # set when the run belongs to an automation


def _run_progress(job: Job) -> int:
    """Percent complete (0–100) — processed (done + errored + cancelled) over the
    task count, matching the live job card's math."""
    if not job.task_count or job.task_count <= 0:
        return 0
    processed = (job.completed_count or 0) + (job.error_count or 0) + (job.cancelled_count or 0)
    return min(100, int(processed / job.task_count * 100))


def _run_label(job: Job) -> str:
    if job.automation is not None and job.automation.is_system:
        return job.automation.display_name
    label = job.message or job.name
    return {
        "Imported {totalCount}/{taskCount} photos": gettext("Import photos"),
        "Indexed {totalCount}/{taskCount} photos": gettext("Index photos"),
        "Processed {totalCount}/{taskCount} media items": gettext("Find duplicates"),
        "import_photos": gettext("Import photos"),
        "index_photos": gettext("Index photos"),
        "find_duplicates": gettext("Find duplicates"),
    }.get(label, label)


def _kind_label(job: Job) -> str:
    """What ran, by job kind, even when an automation started it."""
    return {
        "import_photos": gettext("Import photos"),
        "index_photos": gettext("Index photos"),
        "find_duplicates": gettext("Find duplicates"),
    }.get(job.name, _run_label(job))


def _run_summary(job: Job) -> str:
    """One-line result for a run: progress counts for batch jobs (find_duplicates /
    index), else the job's message (custom runs carry the automation name)."""
    completed = job.completed_count or 0
    errors = job.error_count or 0
    cancelled = job.cancelled_count or 0
    if job.task_count and job.task_count > 1:
        summary = gettext(
            "%(completed)s of %(total)s processed",
            completed=completed,
            total=job.task_count,
        )
        if errors:
            summary += ", " + ngettext(
                "%(count)s error",
                "%(count)s errors",
                errors,
                count=errors,
            )
        if cancelled:
            summary += ", " + ngettext(
                "%(count)s cancelled",
                "%(count)s cancelled",
                cancelled,
                count=cancelled,
            )
        return summary
    return _run_label(job)


def _run_status_label(status: str) -> str:
    return {
        JOB_STATUS_PENDING: gettext("Pending"),
        JOB_STATUS_RUNNING: gettext("Running"),
        JOB_STATUS_COMPLETED: gettext("Completed"),
        JOB_STATUS_CANCELLED: gettext("Cancelled"),
        JOB_STATUS_FAILED: gettext("Failed"),
    }.get(status, status.capitalize())


def _run_status_chip(status: str) -> str:
    return {
        JOB_STATUS_PENDING: "chip-warning",
        JOB_STATUS_RUNNING: "chip-warning",
        JOB_STATUS_COMPLETED: "chip-success",
        JOB_STATUS_FAILED: "chip-danger",
    }.get(status, "")


def run_view(job: Job) -> RunView:
    # A run that finished with some failed items is flagged on its status chip,
    # where it's seen at a glance, not only in the summary's error count.
    completed_with_errors = job.status == JOB_STATUS_COMPLETED and bool(job.error_count)
    return RunView(
        job_id=job.id,
        status=job.status,
        status_label=gettext("Completed with errors") if completed_with_errors else _run_status_label(job.status),
        status_chip="chip-warning" if completed_with_errors else _run_status_chip(job.status),
        is_finished=job.status in RUN_FINISHED_STATUSES,
        is_error=job.status == JOB_STATUS_FAILED or bool(job.error_count) or bool(job.error),
        progress=_run_progress(job),
        started_at=job.started_at or job.created_at,
        finished_at=job.completed_at,
        label=_kind_label(job),
        summary=_run_summary(job),
        error=job.error,
        automation_slug=job.automation.slug if job.automation is not None else None,
    )
