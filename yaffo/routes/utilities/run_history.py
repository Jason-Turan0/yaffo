"""Run history rows: the compact list of past Jobs shown on an automation's detail
page and under the Index Photos job cards. Built from a Job so the template stays
dumb and the per-run-kind display logic lives in one tested place."""
import json
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
from yaffo.utils.file_sync import (
    FILE_SYNC_JOB,
    SYNC_FAILED,
    SYNC_IN_SYNC,
    SYNC_NO_FOLDER_CONNECTED,
    SYNC_NO_MEDIA_DIRS,
    SYNC_NO_THUMBNAIL_DIR,
    SYNC_STARTED,
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
        "file_sync": gettext("File sync"),
    }.get(label, label)


def _kind_label(job: Job) -> str:
    """What ran, by job kind, even when an automation started it."""
    return {
        "import_photos": gettext("Import photos"),
        "index_photos": gettext("Index photos"),
        "find_duplicates": gettext("Find duplicates"),
        "file_sync": gettext("File sync"),
    }.get(job.name, _run_label(job))


def _file_sync_summary(job: Job) -> str:
    """How a file-sync run ended, from the outcome it recorded (utils/file_sync.py)."""
    try:
        data = json.loads(job.job_data or "{}")
    except ValueError:
        data = {}
    outcome = data.get("outcome")
    if outcome == SYNC_IN_SYNC:
        return gettext("Already in sync")
    if outcome == SYNC_STARTED:
        indexed, removed = int(data.get("indexed") or 0), int(data.get("removed") or 0)
        parts = []
        if indexed:
            parts.append(ngettext("Indexing %(count)s new file", "Indexing %(count)s new files", indexed,
                                  count=indexed))
        if removed:
            parts.append(ngettext("removed %(count)s missing item", "removed %(count)s missing items", removed,
                                  count=removed))
        return ", ".join(parts)
    if outcome == SYNC_NO_MEDIA_DIRS:
        return gettext("Skipped: no media folders are configured")
    if outcome == SYNC_NO_THUMBNAIL_DIR:
        return gettext("Skipped: no thumbnail folder is configured")
    if outcome == SYNC_NO_FOLDER_CONNECTED:
        return gettext("Skipped: no media folder is connected")
    if outcome == SYNC_FAILED:
        return gettext("The sync failed")
    return gettext("Running") if job.status in (JOB_STATUS_PENDING, JOB_STATUS_RUNNING) else _run_label(job)


def _run_summary(job: Job) -> str:
    """One-line result for a run: progress counts for batch jobs (find_duplicates /
    index), how a file-sync run ended, else the job's message (custom runs carry
    the automation name)."""
    if job.name == FILE_SYNC_JOB:
        return _file_sync_summary(job)
    if job.automation_id and job.status == JOB_STATUS_COMPLETED and not job.task_count:
        try:
            output = json.loads(job.job_data or "{}").get("output")
        except (ValueError, TypeError, AttributeError):
            output = None
        if output:
            return output
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


def _run_error(job: Job) -> str | None:
    try:
        data = json.loads(job.job_data or "{}")
    except ValueError:
        data = {}
    if data.get("dispatch_error_code") == "invalid_scope":
        return gettext("The scheduled scope is no longer valid. Edit the trigger to choose a configured media directory or a folder inside one.")
    if data.get("dispatch_error_code") == "dispatch_failed":
        return gettext("The scheduled run could not start: %(error)s", error=job.error or "")
    return job.error


@dataclass(frozen=True)
class RunStatus:
    label: str  # translated, e.g. "Completed with errors"
    chip: str   # chip tone modifier


def run_status(status: str, error_count: int | None = 0) -> RunStatus:
    """How a run's status reads on a chip, for the run history and the job card
    alike. A run that finished with some failed items is flagged here, where it's
    seen at a glance, not only in the summary's error count."""
    if status == JOB_STATUS_COMPLETED and error_count:
        return RunStatus(gettext("Completed with errors"), "chip-warning")
    return RunStatus(_run_status_label(status), _run_status_chip(status))


def run_view(job: Job) -> RunView:
    status = run_status(job.status, job.error_count)
    return RunView(
        job_id=job.id,
        status=job.status,
        status_label=status.label,
        status_chip=status.chip,
        is_finished=job.status in RUN_FINISHED_STATUSES,
        is_error=job.status == JOB_STATUS_FAILED or bool(job.error_count) or bool(job.error),
        progress=_run_progress(job),
        started_at=job.started_at or job.created_at,
        finished_at=job.completed_at,
        label=_kind_label(job),
        summary=_run_summary(job),
        error=_run_error(job),
        automation_slug=job.automation.slug if job.automation is not None else None,
    )
