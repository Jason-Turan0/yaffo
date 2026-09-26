"""The assistant's health checks: small pure functions over collected facts.

Each check turns facts (gathered by diagnostics.py) into findings with a level
(`ok`, `warning`, `problem`), a short explanation, and a doc section to read. The
first set comes from real incidents: an unmounted or failing library drive, a
thumbnail folder inside the library, faces linked but not assigned or stuck in
PROCESSING, impossible dates, a stopped task host, pending migrations, missing
tools or models, and automations that keep failing.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Optional

OK = "ok"
WARNING = "warning"
PROBLEM = "problem"
LEVELS = (OK, WARNING, PROBLEM)

DOC_MEDIA_DIRS = "guide/reference-maintenance/settings.md#media-directories"
DOC_THUMBNAILS = "guide/reference-maintenance/settings.md#thumbnail-directory"
DOC_PHOTOS_MISSING = "guide/reference-maintenance/troubleshooting.md#photos-do-not-appear"
DOC_JOBS = "guide/reference-maintenance/troubleshooting.md#jobs-are-stuck-or-slow"
DOC_FACES = "guide/reference-maintenance/troubleshooting.md#faces-labels-or-duplicates-look-wrong"
DOC_REINDEX = "guide/library-basics/indexing-library.md#re-index-after-changes"
DOC_SYSTEM = "guide/reference-maintenance/settings.md#system-information"
DOC_AUTOMATIONS = "guide/create-customize/automations.md"

# A drive with less free space than this is almost full.
LOW_SPACE_FRACTION = 0.05
LOW_SPACE_BYTES = 2 * 1024 ** 3
SLOW_PROBE_SECONDS = 2.0
# Above this share of undated photos, date-based views look empty.
UNDATED_WARNING_SHARE = 0.25
# The task host writes a heartbeat every few seconds; older than this, it's gone.
HEARTBEAT_STALE_SECONDS = 60.0
# An enabled automation whose last N runs all failed.
FAILING_AUTOMATION_RUNS = 3


@dataclass(frozen=True)
class Finding:
    check: str
    level: str
    message: str
    doc: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)


def _ok(check: str, message: str) -> Finding:
    return Finding(check, OK, message)


# ---- media dirs ---------------------------------------------------------

def check_media_dir(label: str, facts: dict, probe: Optional[dict] = None) -> list[Finding]:
    """`facts` from AssistantFS.media_dir_facts (or {"error": ...} when that timed
    out); `probe` from AssistantFS.probe."""
    check = f"media_dir:{label}"
    if facts.get("error"):
        return [Finding(check, PROBLEM, f"Media folder {label} did not respond: {facts['error']}.", DOC_PHOTOS_MISSING)]
    if not facts.get("exists"):
        return [Finding(
            check, PROBLEM,
            f"Media folder {label} is missing or its drive is not mounted. Until it's back, "
            "a sync would treat its photos as deleted.",
            DOC_PHOTOS_MISSING,
        )]
    findings: list[Finding] = []
    if facts.get("readable") is False:
        findings.append(Finding(check, PROBLEM, f"Media folder {label} can't be read (permissions).", DOC_MEDIA_DIRS))
    if probe is not None:
        if not probe.get("responded"):
            findings.append(Finding(
                check, PROBLEM,
                f"Media folder {label} did not respond to a quick listing. The drive may be failing "
                "or asleep; scans will hang on it.",
                DOC_PHOTOS_MISSING,
            ))
        elif probe.get("error"):
            findings.append(Finding(check, PROBLEM, f"Listing media folder {label} failed: {probe['error']}.", DOC_PHOTOS_MISSING))
        elif (probe.get("seconds") or 0) >= SLOW_PROBE_SECONDS:
            findings.append(Finding(
                check, WARNING,
                f"Media folder {label} took {probe['seconds']:.1f}s to list its first entries; the drive is slow.",
                DOC_PHOTOS_MISSING,
            ))
    if str(facts.get("filesystem_type") or "").lower() == "exfat":
        findings.append(Finding(check, WARNING,
            f"Media folder {label} is on exFAT. If scans are slow or fail, check the drive and keep a backup.",
            DOC_PHOTOS_MISSING))
    total, free = facts.get("total_bytes"), facts.get("free_bytes")
    if total and free is not None and (free < LOW_SPACE_BYTES or free / total < LOW_SPACE_FRACTION):
        findings.append(Finding(
            check, WARNING,
            f"The drive holding media folder {label} is almost full ({format_size(free)} free).",
            DOC_MEDIA_DIRS,
        ))
    return findings or [_ok(check, f"Media folder {label} is available.")]


def check_no_media_dirs(count: int) -> list[Finding]:
    if count:
        return []
    return [Finding("media_dirs", WARNING, "No media folders are configured, so nothing gets indexed.", DOC_MEDIA_DIRS)]


# ---- thumbnails ---------------------------------------------------------

def check_thumbnail_dir(configured: bool, exists: bool, inside_media_dir: bool, has_marker: bool) -> list[Finding]:
    check = "thumbnail_dir"
    if not configured:
        return [_ok(check, "Using the default thumbnail folder.")]
    if not exists:
        return [Finding(check, PROBLEM, "The thumbnail folder is missing; faces and video posters won't show.", DOC_THUMBNAILS)]
    if inside_media_dir and not has_marker:
        return [Finding(
            check, PROBLEM,
            "The thumbnail folder is inside a media folder without its marker file, so face crops "
            "and posters can be indexed as photos.",
            DOC_THUMBNAILS,
        )]
    return [_ok(check, "The thumbnail folder is in place.")]


# ---- faces --------------------------------------------------------------

def check_faces(linked_not_assigned: int, processing: int, face_tasks_queued: int, ignored_linked: int) -> list[Finding]:
    findings: list[Finding] = []
    if linked_not_assigned:
        findings.append(Finding(
            "faces:linked_not_assigned", PROBLEM,
            f"{linked_not_assigned} face(s) are linked to a person but not marked assigned, so they "
            "show up again for review.",
            DOC_FACES,
        ))
    if processing and not face_tasks_queued:
        findings.append(Finding(
            "faces:stuck_processing", PROBLEM,
            f"{processing} face(s) are stuck in PROCESSING with no face task queued or running.",
            DOC_FACES,
        ))
    if ignored_linked:
        findings.append(Finding(
            "faces:ignored_linked", WARNING,
            f"{ignored_linked} ignored face(s) are still linked to a person.",
            DOC_FACES,
        ))
    return findings or [_ok("faces", "Face statuses are consistent.")]


# ---- dates --------------------------------------------------------------

def check_dates(implausible: int, undated: int, total: int) -> list[Finding]:
    findings: list[Finding] = []
    if implausible:
        findings.append(Finding(
            "dates:implausible", WARNING,
            f"{implausible} item(s) have an impossible date (before 1900 or in the future); they sort "
            "to the wrong place.",
            DOC_REINDEX,
        ))
    if total and undated / total >= UNDATED_WARNING_SHARE:
        findings.append(Finding(
            "dates:undated", WARNING,
            f"{undated} of {total} item(s) have no date, so they're missing from date views.",
            DOC_REINDEX,
        ))
    return findings or [_ok("dates", "Dates look plausible.")]


# ---- jobs and the task queue -------------------------------------------

def check_worker(heartbeat_age: Optional[float], running_jobs: int) -> list[Finding]:
    check = "worker"
    if heartbeat_age is None:
        level = PROBLEM if running_jobs else WARNING
        return [Finding(
            check, level,
            "The background task host has never reported in, so background jobs won't run."
            + (f" {running_jobs} job(s) are marked running." if running_jobs else ""),
            DOC_JOBS,
        )]
    if heartbeat_age > HEARTBEAT_STALE_SECONDS:
        return [Finding(
            check, PROBLEM,
            f"The background task host last reported {format_duration(heartbeat_age)} ago; it has probably stopped."
            + (f" {running_jobs} job(s) are marked running but nothing is working on them." if running_jobs else ""),
            DOC_JOBS,
        )]
    return [_ok(check, "The background task host is running.")]


def check_failed_tasks(failed_by_name: dict[str, int]) -> list[Finding]:
    if not failed_by_name:
        return [_ok("tasks", "No background tasks failed in the last 24 hours.")]
    total = sum(failed_by_name.values())
    names = ", ".join(f"{name} ({count})" for name, count in sorted(failed_by_name.items(), key=lambda i: -i[1])[:5])
    return [Finding("tasks", WARNING, f"{total} background task(s) failed in the last 24 hours: {names}.", DOC_JOBS)]


# ---- install -----------------------------------------------------------

def check_install(host_started: Optional[float], code_changed: Optional[float]) -> list[Finding]:
    """A task host started before its code last changed is running an old build."""
    if host_started is not None and code_changed is not None and host_started < code_changed:
        return [Finding(
            "install:stale_host", WARNING,
            "The background task host started before Yaffo's code last changed, so it may be running "
            "old code. Restart Yaffo.",
            DOC_SYSTEM,
        )]
    return []


def check_migrations(pending: list[str]) -> list[Finding]:
    if not pending:
        return [_ok("migrations", "The database schema is up to date.")]
    return [Finding(
        "migrations", PROBLEM,
        f"{len(pending)} database migration(s) haven't been applied; restart Yaffo to apply them.",
        DOC_SYSTEM,
    )]


def check_tools(exiftool: bool, ffmpeg: bool, face_model: bool, clip_model: bool) -> list[Finding]:
    findings: list[Finding] = []
    if not exiftool:
        findings.append(Finding("tools:exiftool", WARNING, "exiftool wasn't found; dates and metadata are read less completely.", DOC_SYSTEM))
    if not ffmpeg:
        findings.append(Finding("tools:ffmpeg", WARNING, "ffmpeg wasn't found; videos get no posters or faces.", DOC_SYSTEM))
    if not face_model:
        findings.append(Finding("tools:face_model", PROBLEM, "The face recognition model isn't downloaded yet; faces won't be detected.", DOC_SYSTEM))
    if not clip_model:
        findings.append(Finding("tools:clip_model", WARNING, "The photo-label model isn't downloaded yet; photos won't be labeled.", DOC_SYSTEM))
    return findings or [_ok("tools", "exiftool, ffmpeg and the models are available.")]


# ---- automations -------------------------------------------------------

def check_automations(recent_outcomes: dict[str, list[str]]) -> list[Finding]:
    """`recent_outcomes` maps an enabled automation's name to its latest run
    statuses, newest first."""
    failing = [
        name for name, outcomes in recent_outcomes.items()
        if len(outcomes) >= FAILING_AUTOMATION_RUNS
        and all(o == "FAILED" for o in outcomes[:FAILING_AUTOMATION_RUNS])
    ]
    if not failing:
        return [_ok("automations", "No enabled automation keeps failing.")]
    return [Finding(
        "automations", WARNING,
        f"These automations failed their last {FAILING_AUTOMATION_RUNS} runs: {', '.join(sorted(failing))}.",
        DOC_AUTOMATIONS,
    )]


# ---- formatting --------------------------------------------------------

def overall(findings: list[Finding]) -> str:
    if any(f.level == PROBLEM for f in findings):
        return PROBLEM
    if any(f.level == WARNING for f in findings):
        return WARNING
    return OK


def format_size(num_bytes: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if num_bytes < 1024 or unit == "TB":
            return f"{num_bytes:.1f} {unit}" if unit != "B" else f"{int(num_bytes)} B"
        num_bytes /= 1024
    return f"{num_bytes:.1f} TB"


def format_duration(seconds: float) -> str:
    if seconds < 120:
        return f"{int(seconds)}s"
    if seconds < 7200:
        return f"{int(seconds // 60)} min"
    if seconds < 172800:
        return f"{int(seconds // 3600)} h"
    return f"{int(seconds // 86400)} days"
