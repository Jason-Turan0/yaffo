"""Run history rows: the compact list of past Jobs shown on an automation's detail
page and under the Index Photos job cards. Built from a Job so the template stays
dumb and the per-run-kind display logic lives in one tested place.

`run_views` builds a whole list. There, a file sync that started work shows that
work: the import and index Jobs it queued fold into its row (status, progress,
time left, and one Cancel for both), and back-to-back "Already in sync" checks
collapse into a single row."""
import json
import math
from dataclasses import dataclass, replace
from datetime import datetime

from flask_babel import gettext, ngettext
from sqlalchemy.orm import Session

from yaffo.db.models import (
    Job,
    JOB_STATUS_CANCELLED,
    JOB_STATUS_COMPLETED,
    JOB_STATUS_FAILED,
    JOB_STATUS_PENDING,
    JOB_STATUS_RUNNING,
)
from yaffo.utils.time import utcnow
from yaffo.utils.file_sync import (
    FILE_SYNC_JOB,
    SYNC_FAILED,
    SYNC_CANCELLED,
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
    stage_note: str | None = None  # which step of a file sync's pipeline is running
    eta_note: str | None = None    # "about 12 minutes left", from estimated_completed_at
    repeat_count: int = 1          # quiet in-sync checks folded into this row
    repeat_since: datetime | None = None  # the oldest of those checks


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
    if outcome == SYNC_CANCELLED:
        return gettext("Cancelled")
    return gettext("Running") if job.status in (JOB_STATUS_PENDING, JOB_STATUS_RUNNING) else _run_label(job)


def _run_summary(job: Job) -> str:
    """One-line result for a run: file-sync outcome, completed automation output,
    batch progress counts, or the job's message."""
    if job.name == FILE_SYNC_JOB:
        return _file_sync_summary(job)
    if job.automation_id and job.status == JOB_STATUS_COMPLETED:
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


def run_status(status: str, error_count: int | None = 0, stopped: bool = True) -> RunStatus:
    """How a run's status reads on a chip, for the run history and the job card
    alike. A run that finished with some failed items is flagged here, where it's
    seen at a glance, not only in the summary's error count. A cancelled run whose
    work hasn't ended yet (`stopped` False: no completed_at) reads Stopping."""
    if status == JOB_STATUS_CANCELLED and not stopped:
        return RunStatus(gettext("Stopping"), "chip-warning")
    if status == JOB_STATUS_COMPLETED and error_count:
        return RunStatus(gettext("Completed with errors"), "chip-warning")
    return RunStatus(_run_status_label(status), _run_status_chip(status))


def run_view(job: Job) -> RunView:
    status = run_status(job.status, job.error_count, stopped=job.completed_at is not None)
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


# ---- whole lists: file sync pipelines and repeated in-sync checks -------------------------

_IN_PROGRESS = (JOB_STATUS_PENDING, JOB_STATUS_RUNNING)


def _job_data(job: Job) -> dict:
    try:
        data = json.loads(job.job_data or "{}")
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _pipeline_ids(job: Job) -> tuple[str | None, str | None]:
    """The (import, index) Job ids a file sync queued, or (None, None)."""
    if job.name != FILE_SYNC_JOB:
        return None, None
    data = _job_data(job)
    if data.get("outcome") != SYNC_STARTED:
        return None, None
    return data.get("import_job_id"), data.get("index_job_id")


def pipeline_job_ids(job: Job) -> list[str]:
    """The Jobs a file sync run started; cancelling the run cancels these."""
    return [job_id for job_id in _pipeline_ids(job) if job_id]


def _eta_note(eta: datetime | None, now: datetime) -> str | None:
    """Time left until `eta`: minutes under an hour; hours and minutes (to the
    nearest 5) up to ten hours; whole hours beyond that, where minutes are noise.
    None once the estimate is more than a minute past: a stalled job has outrun its
    estimate, so the row stops promising a finish."""
    if eta is None or (eta - now).total_seconds() < -60:
        return None
    minutes = math.ceil((eta - now).total_seconds() / 60)
    if minutes <= 1:
        return gettext("less than a minute left")
    if minutes < 60:
        return ngettext("about %(num)d minute left", "about %(num)d minutes left", minutes)
    if minutes < 600:
        # minutes / 5 never lands on .5, so round() can't hit its round-half-to-even case.
        hours, rest = divmod(5 * round(minutes / 5), 60)
        if rest:
            return gettext("about %(hours)d h %(minutes)d min left", hours=hours, minutes=rest)
        return ngettext("about %(num)d hour left", "about %(num)d hours left", hours)
    hours = minutes // 60
    return ngettext("about %(num)d hour left", "about %(num)d hours left", hours)


def _pipeline_view(view: RunView, import_job: Job | None, index_job: Job | None, now: datetime) -> RunView:
    """A started file sync's row, read from the import and index Jobs it queued. The
    sync's own Job closes as soon as it has queued them, so on its own it would say
    Completed while photos are still being indexed."""
    stages = [job for job in (import_job, index_job) if job is not None]
    if not stages:  # dismissed from the job cards; the sync's own record is all there is
        return view
    active = next((job for job in stages if job.status in _IN_PROGRESS), None)
    if active is not None:
        status = run_status(JOB_STATUS_RUNNING)
        return replace(
            view, status=JOB_STATUS_RUNNING, status_label=status.label, status_chip=status.chip,
            is_finished=False, progress=_run_progress(active), finished_at=None,
            stage_note=gettext("importing") if active is import_job else (
                gettext("import done, indexing") if import_job is not None else gettext("indexing")),
            eta_note=_eta_note(active.estimated_completed_at, now),
        )
    statuses = {job.status for job in stages}
    overall = (JOB_STATUS_CANCELLED if JOB_STATUS_CANCELLED in statuses
               else JOB_STATUS_FAILED if JOB_STATUS_FAILED in statuses
               else view.status)
    errors = sum(job.error_count or 0 for job in stages)
    stopped = all(job.completed_at is not None for job in stages if job.status == JOB_STATUS_CANCELLED)
    status = run_status(overall, errors, stopped=stopped)
    return replace(
        view, status=overall, status_label=status.label, status_chip=status.chip,
        is_error=view.is_error or overall == JOB_STATUS_FAILED or bool(errors),
        finished_at=max((job.completed_at for job in stages if job.completed_at), default=view.finished_at),
    )


def _quiet_in_sync(job: Job) -> bool:
    """A sync that found nothing to do and nothing wrong: the hourly no-op."""
    return (job.name == FILE_SYNC_JOB and job.status == JOB_STATUS_COMPLETED and not job.error
            and _job_data(job).get("outcome") == SYNC_IN_SYNC)


def run_views(session: Session, jobs: list[Job], limit: int | None = None) -> list[RunView]:
    """Rows for `jobs` (newest first). A started file sync takes over the import and
    index Jobs it queued, which then get no rows of their own; consecutive quiet
    in-sync checks become one row counting them. `limit` caps the rows, not the jobs,
    so callers pass more jobs than rows they want."""
    linked_ids = {job_id for job in jobs for job_id in pipeline_job_ids(job)}
    linked = {job.id: job for job in session.query(Job).filter(Job.id.in_(linked_ids))} if linked_ids else {}
    now = utcnow()
    views: list[RunView] = []
    previous_quiet = False
    for job in jobs:
        if job.id in linked_ids:
            continue
        quiet = _quiet_in_sync(job)
        if quiet and previous_quiet:
            views[-1] = replace(views[-1], repeat_count=views[-1].repeat_count + 1,
                                repeat_since=job.started_at or job.created_at)
            continue
        previous_quiet = quiet
        view = run_view(job)
        import_id, index_id = _pipeline_ids(job)
        if import_id or index_id:
            view = _pipeline_view(view, linked.get(import_id), linked.get(index_id), now)
        views.append(view)
    return views[:limit] if limit is not None else views
