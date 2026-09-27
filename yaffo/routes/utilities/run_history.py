"""Run history rows and job cards: how a Job reads to the user.

A Job stores codes, not text (utils/job_codes.py): its kind in `name`, how it
ended in `job_data["outcome"]`, why it needs attention in `job_data["problem"]`,
each with the numbers its sentence needs. Everything here translates those at
render time, so a run reads in the viewer's language. English diagnostics
(`Job.error`) and tuning values (`job_data["details"]`) only go under a row's
Details and to the assistant. A custom script's output is its own content and is
shown as-is.

`run_views` builds a whole list. There, a file sync that started work shows that
work: the import and index Jobs it queued fold into its row (status, progress,
time left, and one Cancel for both), and back-to-back "Already in sync" checks
collapse into a single row."""
import math
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any

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
from yaffo.utils import job_codes as codes
from yaffo.utils.time import utcnow
from yaffo.utils.file_sync import (
    FILE_SYNC_JOB,
    SYNC_FAILED,
    SYNC_CANCELLED,
    SYNC_IN_SYNC,
    SYNC_NO_FOLDER_CONNECTED,
    SYNC_NO_MEDIA_DIRS,
    SYNC_NO_THUMBNAIL_DIR,
    SYNC_SKIPPED,
    SYNC_STARTED,
)

RUN_FINISHED_STATUSES = (JOB_STATUS_COMPLETED, JOB_STATUS_FAILED, JOB_STATUS_CANCELLED)

# Longest English error passed to "Ask Yaffo" from a row.
ASSISTANT_ERROR_CHARS = 500


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
    problem: str | None    # translated: why the run needs attention (the red line)
    details: list[tuple[str, str]]  # English diagnostics and tuning values, for Details
    assistant_error: str   # English, for "Ask Yaffo": problem code and error text
    automation_slug: str | None  # set when the run belongs to an automation
    stage_note: str | None = None  # which step of a file sync's pipeline is running
    eta_note: str | None = None    # "about 12 minutes left", from estimated_completed_at
    repeat_count: int = 1          # quiet in-sync checks folded into this row
    repeat_since: datetime | None = None  # the oldest of those checks


def _field(job: Any, name: str) -> Any:
    """A Job column, from the Job or from its to_dict_with_view_props (job cards
    get either)."""
    return job.get(name) if isinstance(job, dict) else getattr(job, name, None)


def _run_progress(job: Job) -> int:
    """Percent complete (0–100) — processed (done + errored + cancelled) over the
    task count, matching the live job card's math."""
    if not job.task_count or job.task_count <= 0:
        return 0
    processed = (job.completed_count or 0) + (job.error_count or 0) + (job.cancelled_count or 0)
    return min(100, int(processed / job.task_count * 100))


# ---- what ran ----------------------------------------------------------------------------

def _kind_labels() -> dict[str, str]:
    return {
        "import_photos": gettext("Import photos"),
        "index_photos": gettext("Index photos"),
        "find_duplicates": gettext("Find duplicates"),
        "remove_duplicates": gettext("Remove duplicates"),
        FILE_SYNC_JOB: gettext("File sync"),
    }


def _run_label(job: Job) -> str:
    """What ran: the automation's name for its runs, else the job kind's label."""
    if job.automation is not None:
        return job.automation.display_name
    return _kind_labels().get(job.name, job.name)


def _kind_label(job: Job) -> str:
    """What ran, by job kind, even when an automation started it (Index Photos
    lists import and index runs of the file-sync automation)."""
    return _kind_labels().get(job.name) or _run_label(job)


def job_progress_text(job: Any) -> str:
    """A job card's progress line, e.g. "Indexed 12/40 photos". `job` is a Job or
    its to_dict_with_view_props."""
    name = _field(job, "name")
    done = sum(int(_field(job, key) or 0) for key in ("completed_count", "error_count", "cancelled_count"))
    total = int(_field(job, "task_count") or 0)
    if name == "import_photos":
        return gettext("Imported %(done)s/%(total)s photos", done=done, total=total)
    if name == "index_photos":
        return gettext("Indexed %(done)s/%(total)s photos", done=done, total=total)
    if name == "find_duplicates":
        return gettext("Processed %(done)s/%(total)s media items", done=done, total=total)
    if name == "remove_duplicates":
        data = codes.load_job_data(_field(job, "job_data"))
        action = data.get("action_type")
        if action == "trash":
            return gettext("Moved %(done)s/%(total)s files to the trash", done=done, total=total)
        if action == "delete":
            return gettext("Deleted %(done)s/%(total)s files", done=done, total=total)
        if action == "moveFolder":
            return gettext("Moved %(done)s/%(total)s files to %(destination)s", done=done, total=total,
                           destination=data.get("destination_folder") or "")
        return gettext("Processed %(done)s/%(total)s files", done=done, total=total)
    return _kind_labels().get(name, name or "")


# ---- how it ended ------------------------------------------------------------------------

def _file_sync_summary(job: Job, data: dict) -> str:
    """How a file-sync run ended, from the outcome it recorded (utils/file_sync.py)."""
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
        return gettext("Cancelled before changing anything")
    return gettext("Running") if job.status in (JOB_STATUS_PENDING, JOB_STATUS_RUNNING) else _run_label(job)


def _count(data: dict, key: str) -> int:
    try:
        return int(data.get(key) or 0)
    except (TypeError, ValueError):
        return 0


def _automation_outcome(data: dict) -> str | None:
    """A system automation's result sentence, from its outcome code."""
    outcome, total = data.get("outcome"), _count(data, "total")
    if outcome == codes.OUTCOME_NO_MEDIA:
        return gettext("No indexed media to process")
    if outcome == codes.OUTCOME_LABELED:
        return ngettext("Labeled %(labeled)s of %(num)d photo", "Labeled %(labeled)s of %(num)d photos",
                        total, labeled=_count(data, "labeled"))
    if outcome == codes.OUTCOME_ASSIGNED:
        faces = _count(data, "faces")
        return ngettext("Assigned %(num)d face", "Assigned %(num)d faces", faces)
    if outcome == codes.OUTCOME_NAMED:
        return ngettext("Named the location of %(named)s of %(num)d photo",
                        "Named the location of %(named)s of %(num)d photos", total, named=_count(data, "named"))
    if outcome == codes.OUTCOME_GEOTAGGED:
        return ngettext("Geotagged %(geotagged)s of %(num)d photo", "Geotagged %(geotagged)s of %(num)d photos",
                        total, geotagged=_count(data, "geotagged"))
    if outcome == codes.OUTCOME_WRITTEN:
        return ngettext("Wrote metadata to %(written)s of %(num)d file",
                        "Wrote metadata to %(written)s of %(num)d files", total, written=_count(data, "written"))
    return None


def _output_lines(data: dict) -> list[str]:
    """A run's printed output as its non-blank lines (a list, or one string)."""
    output = data.get("output") or []
    lines = output if isinstance(output, list) else str(output).splitlines()
    return [str(line) for line in lines if str(line).strip()]


def _is_custom_run(job: Any) -> bool:
    automation = None if isinstance(job, dict) else job.automation
    return automation is not None and not automation.is_system


def _script_output(job: Job, data: dict) -> str | None:
    """A custom script's summary: the last line it printed (scripts print their
    result last), shown as-is -- it's the script's own content. The full output
    is under Details."""
    if not _is_custom_run(job):
        return None
    lines = _output_lines(data)
    return lines[-1] if lines else None


def _run_summary(job: Job, data: dict) -> str:
    """One-line result for a run: file-sync outcome, a completed automation's
    outcome (or a custom script's output), else batch progress counts."""
    if job.name == FILE_SYNC_JOB:
        return _file_sync_summary(job, data)
    if job.automation_id and job.status == JOB_STATUS_COMPLETED:
        text = _automation_outcome(data) or _script_output(job, data)
        if text:
            return text
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


# ---- why it needs attention --------------------------------------------------------------

def _problem_code(job: Any, data: dict) -> str | None:
    return data.get("problem") or data.get("dispatch_error_code")


def problem_text(job: Any, data: dict | None = None) -> str | None:
    """The translated line saying why a run needs attention, or None. `job` is a
    Job or its to_dict_with_view_props. A failed run with no recorded problem (one
    from before problems were recorded) reads as a generic failure; a skipped file
    sync's summary already says why."""
    data = codes.load_job_data(_field(job, "job_data")) if data is None else data
    code, params = _problem_code(job, data), data.get("problem_params") or {}
    if code == codes.PROBLEM_MEDIA_FOLDER_EMPTY:
        count = int(params.get("count") or 0)
        return ngettext(
            "These media folders hold no files, so %(num)d item under them was left alone: %(roots)s. "
            "Check the drive is connected, or remove the folder in Settings.",
            "These media folders hold no files, so %(num)d items under them were left alone: %(roots)s. "
            "Check the drive is connected, or remove the folder in Settings.",
            count, roots=", ".join(params.get("roots") or []))
    if code == codes.PROBLEM_ITEMS_UNPROCESSED:
        count = int(params.get("count") or 0)
        return ngettext("%(num)d item was never processed because a background worker stopped unexpectedly.",
                        "%(num)d items were never processed because a background worker stopped unexpectedly.",
                        count)
    if code == codes.PROBLEM_WORKER_STOPPED:
        return gettext("The background worker stopped unexpectedly.")
    if code == codes.PROBLEM_TASK_ERROR:
        return gettext("The run stopped with an error.")
    if code == codes.PROBLEM_SCRIPT_ERROR:
        return gettext("The script stopped with an error.")
    if code == codes.PROBLEM_SCRIPT_TIMEOUT:
        return gettext("The script ran past its time limit.")
    if code == codes.PROBLEM_SCRIPT_CALL_LIMIT:
        return gettext("The script made too many calls.")
    if code == codes.PROBLEM_INVALID_SCOPE:
        return gettext("The scheduled scope is no longer valid. Edit the trigger to choose a configured "
                       "media directory or a folder inside one.")
    if code in (codes.PROBLEM_DISPATCH_FAILED, codes.PROBLEM_EVENT_DISPATCH_FAILED):
        return gettext("The scheduled run could not start.") if code == codes.PROBLEM_DISPATCH_FAILED \
            else gettext("The run could not start.")
    if _field(job, "status") == JOB_STATUS_FAILED and _field(job, "name") != FILE_SYNC_JOB:
        return gettext("The run stopped with an error.")
    return None


def job_details(job: Any, data: dict | None = None) -> list[tuple[str, str]]:
    """What goes under a row's Details: English diagnostics, tuning values, and a
    system automation's pre-codes summary sentence. Labels are English on purpose:
    this is for debugging and for the assistant, like the values."""
    data = codes.load_job_data(_field(job, "job_data")) if data is None else data
    rows: list[tuple[str, str]] = []
    if _field(job, "error"):
        rows.append(("error", str(_field(job, "error"))))
    for key, value in (data.get("details") or {}).items():
        rows.append((str(key), str(value)))
    lines = _output_lines(data)
    if _is_custom_run(job):
        # The full printed output, unless the summary already shows all of it.
        if len(lines) > 1 or (lines and _field(job, "status") != JOB_STATUS_COMPLETED):
            rows.append(("output", "\n".join(lines)))
    elif lines and not data.get("outcome"):
        rows.append(("output", "\n".join(lines)))  # a system run recorded before outcome codes
    return rows


def _assistant_error(job: Job, data: dict, summary: str) -> str:
    parts = [part for part in (_problem_code(job, data), job.error) if part]
    return (": ".join(parts) or summary)[:ASSISTANT_ERROR_CHARS]


# ---- status --------------------------------------------------------------------------------

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


@dataclass(frozen=True)
class RunStatus:
    label: str  # translated, e.g. "Completed with errors"
    chip: str   # chip tone modifier


def run_status(status: str, error_count: int | None = 0, stopped: bool = True, skipped: bool = False) -> RunStatus:
    """How a run's status reads on a chip, for the run history and the job card
    alike. A run that finished with some failed items is flagged here, where it's
    seen at a glance, not only in the summary's error count. A cancelled run whose
    work hasn't ended yet (`stopped` False: no completed_at) reads Stopping. A file
    sync that didn't run because a precondition wasn't met (`skipped`, from its
    outcome) reads Skipped."""
    if skipped:
        return RunStatus(gettext("Skipped"), "chip-warning")
    if status == JOB_STATUS_CANCELLED and not stopped:
        return RunStatus(gettext("Stopping"), "chip-warning")
    if status == JOB_STATUS_COMPLETED and error_count:
        return RunStatus(gettext("Completed with errors"), "chip-warning")
    return RunStatus(_run_status_label(status), _run_status_chip(status))


def run_view(job: Job) -> RunView:
    data = codes.load_job_data(job.job_data)
    skipped = job.name == FILE_SYNC_JOB and data.get("outcome") in SYNC_SKIPPED
    status = run_status(job.status, job.error_count, stopped=job.completed_at is not None, skipped=skipped)
    summary = _run_summary(job, data)
    problem = problem_text(job, data)
    return RunView(
        job_id=job.id,
        status=job.status,
        status_label=status.label,
        status_chip=status.chip,
        is_finished=job.status in RUN_FINISHED_STATUSES,
        is_error=job.status == JOB_STATUS_FAILED or bool(job.error_count) or bool(problem) or bool(job.error),
        progress=_run_progress(job),
        started_at=job.started_at or job.created_at,
        finished_at=job.completed_at,
        label=_kind_label(job),
        summary=summary,
        problem=problem,
        details=job_details(job, data),
        assistant_error=_assistant_error(job, data, summary),
        automation_slug=job.automation.slug if job.automation is not None else None,
    )


# ---- whole lists: file sync pipelines and repeated in-sync checks -------------------------

_IN_PROGRESS = (JOB_STATUS_PENDING, JOB_STATUS_RUNNING)


def _job_data(job: Job) -> dict:
    return codes.load_job_data(job.job_data)


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
            stage_note=gettext("Importing") if active is import_job else gettext("Indexing"),
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
