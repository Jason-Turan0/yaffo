"""The assistant's diagnostic tools: native, read-only checks of this install.

Not host functions: scripts can't call them and automations don't get them (see
docs/development/ai-assistant.md → *Why tools, not host functions*). Each tool
belongs to a group that Settings can switch off (logs, library, files, jobs); the
overview tools are offered whenever any group is on. A switched-off tool is not
offered to the model at all.

Every result is plain text for the model, passed through the redactor, capped,
and wrapped as data. The same redacted text goes to the chat as the activity
line's detail, so the user sees exactly what was sent.
"""
from __future__ import annotations

import json
import os
import platform
import re
import sqlite3
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Optional

from sqlalchemy import func, or_, text
from sqlalchemy.orm import Session

import yaffo
from yaffo import config as app_config
from yaffo.db.models import (
    Album,
    AlbumItem,
    Automation,
    Face,
    FACE_STATUS_ASSIGNED,
    FACE_STATUS_IGNORED,
    FACE_STATUS_PROCESSING,
    Job,
    JOB_STATUS_RUNNING,
    MediaItem,
    Person,
    PersonFace,
    Tag,
)
from yaffo.db.repositories import media_dir_repository
from yaffo.i18n import get_saved_locale
from yaffo.scripts.db.migrate import bundled_migrations, migration_number
from yaffo.site_agents.assistant.tool_providers.diagnostics import health
from yaffo.site_agents.assistant.tool_providers.diagnostics.fs import LOG_NAMES, AssistantFS, FsError, run_with_timeout
from yaffo.site_agents.assistant.redact import Redactor
from yaffo.site_agents.assistant.schemas import ToolActivity
from yaffo.site_agents.assistant.settings import DIAG_FILES, DIAG_JOBS, DIAG_LIBRARY, DIAG_LOGS, DIAG_METADATA
from yaffo.site_agents.common.tool_providers.tool_provider_types import (
    CallToolReturn,
    RawToolDefinition,
    ToolProvider,
    ToolResult,
)
from yaffo.site_agents.common.tool_providers.utils import truncate_tool_result
from yaffo.taskq.store import STATUS_READY, STATUS_RUNNING, Store
from yaffo.utils.exiftool_path import is_exiftool_available
from yaffo.utils.ffmpeg_path import is_ffmpeg_available
from yaffo.utils.settings import get_thumbnail_dir
from yaffo.utils.thumbnail_marker import THUMBNAIL_DIR_MARKER

OVERVIEW = "overview"

MAX_RESULT_CHARS = 12000
MAX_JOBS = 50
MAX_SAMPLE_IDS = 10
MAX_OUTLIERS = 50
MAX_ERROR_GROUPS = 30
MAX_THUMBNAIL_FILES = 20000
QUICK_CHECK_SECONDS = 10.0
FACE_TASK_NAMES = ["assign_faces_to_person", "auto_assign_faces_automation_task"]
_CONFIG_SECRET = re.compile(r"(?i)key|token|secret|password")
_LOG_LINE = re.compile(
    r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)(?:,\d+)? - (\S+) - (\S+) - (ERROR|WARNING|CRITICAL) - (.*)$"
)
_LOG_TIME_FORMAT = "%Y-%m-%d %H:%M:%S"


def _schema(properties: Optional[dict] = None, required: Optional[list[str]] = None) -> dict:
    return {
        "type": "object",
        "properties": properties or {},
        "required": required or [],
        "additionalProperties": False,
    }


_MEDIA_DIR_ID = {"type": "string", "description": "A media folder id from media_dir_status."}
_RELATIVE_PATH = {
    "type": "string",
    "description": "A path relative to the media folder, e.g. '2019/08'. Empty for the folder itself.",
}


@dataclass(frozen=True)
class Output:
    """A handler's result: the model text, the count for the activity line, and
    whether the check itself failed."""
    text: str
    count: int = 0
    error: bool = False


@dataclass(frozen=True)
class DiagnosticTool:
    name: str
    group: str
    description: str
    schema: dict = field(default_factory=_schema)


TOOLS: tuple[DiagnosticTool, ...] = (
    # ---- overview ----
    DiagnosticTool(
        "health_report", OVERVIEW,
        "Run every known health check (media folders, thumbnails, faces, dates, the task host, "
        "failed tasks, migrations, tools and models, failing automations) and report each as "
        "ok / warning / problem with a doc section to read. The best first call for 'something is wrong'.",
    ),
    DiagnosticTool(
        "install_info", OVERVIEW,
        "Version, install kind and code location, when the task host started vs when the code last "
        "changed, Python, platform, and the AI provider and model. Never keys.",
    ),
    DiagnosticTool(
        "settings_summary", OVERVIEW,
        "Media folders (with their ids), the thumbnail folder, language, enabled automations, and "
        "the non-secret config.toml values.",
    ),
    DiagnosticTool("migration_status", OVERVIEW, "Database migrations shipped with this build vs applied."),
    DiagnosticTool(
        "capture_date_source", DIAG_METADATA,
        "Re-read only capture-date metadata for one indexed item. Compare the current EXIF or filename "
        "candidate with the stored date; this does not prove its historical source. No pixels or writes.",
        _schema({"media_item_id": {"type": "integer"}}, ["media_item_id"]),
    ),
    # ---- library ----
    DiagnosticTool(
        "library_stats", DIAG_LIBRARY,
        "Counts by media type, index status and face status; people, tags and albums; the date range, "
        "undated items and impossible dates.",
    ),
    DiagnosticTool(
        "media_item_report", DIAG_LIBRARY,
        "Everything about one photo or video: its path within its media folder, whether the file "
        "exists, index status, date, faces with their status and person, tags, albums, and whether "
        "its thumbnails exist.",
        _schema({"media_item_id": {"type": "integer"}}, ["media_item_id"]),
    ),
    DiagnosticTool(
        "face_consistency", DIAG_LIBRARY,
        "Counts and sample ids for each inconsistent face state: linked to a person but not ASSIGNED, "
        "stuck PROCESSING, ignored but still linked.",
    ),
    DiagnosticTool(
        "date_outliers", DIAG_LIBRARY,
        "Photos with impossible dates (before 1900 or more than a year in the future), with file names.",
        _schema({"limit": {"type": "integer", "minimum": 1, "maximum": MAX_OUTLIERS}}),
    ),
    DiagnosticTool(
        "db_quick_check", DIAG_LIBRARY,
        "SQLite integrity quick check of the library database and the size of its write-ahead log. "
        "Read-only and time-limited.",
    ),
    # ---- logs ----
    DiagnosticTool(
        "ai_call_summary", DIAG_LOGS,
        "Recent AI calls: feature, model, success, duration and cost. No prompts or responses.",
        _schema({"limit": {"type": "integer", "minimum": 1, "maximum": 50}}),
    ),
    DiagnosticTool(
        "recent_errors", DIAG_LOGS,
        "ERROR and WARNING lines from both logs, grouped by message with counts and first/last seen. "
        "Usually the best first look at the logs.",
        _schema({
            "since_hours": {"type": "integer", "minimum": 1, "maximum": 720,
                            "description": "How far back to look (default 24)."},
            "limit": {"type": "integer", "minimum": 1, "maximum": MAX_ERROR_GROUPS},
        }),
    ),
    DiagnosticTool(
        "read_log", DIAG_LOGS,
        "The last lines of yaffo.log (the web app) or background_tasks.log (indexing, faces, "
        "automations), optionally only lines containing some text.",
        _schema({
            "name": {"type": "string", "enum": list(LOG_NAMES)},
            "tail": {"type": "integer", "minimum": 1, "maximum": 500, "description": "Lines to return (default 200)."},
            "contains": {"type": "string", "description": "Only lines containing this text (case-insensitive)."},
        }, ["name"]),
    ),
    # ---- files ----
    DiagnosticTool(
        "media_dir_status", DIAG_FILES,
        "Each media folder: its id, whether it exists, is on a separate (mounted) volume, is "
        "readable/writable, and its free space.",
    ),
    DiagnosticTool(
        "probe_media_dir", DIAG_FILES,
        "Time a quick listing of a media folder's root; reports the latency or 'did not respond in 5s'. "
        "Catches a failing or sleeping drive before a scan hangs on it.",
        _schema({"media_dir_id": _MEDIA_DIR_ID}, ["media_dir_id"]),
    ),
    DiagnosticTool(
        "thumbnail_dir_status", DIAG_FILES,
        "The thumbnail folder: whether it exists, sits inside a media folder, has its marker file, "
        "how many files it holds, and how many aren't used by any face or video.",
    ),
    DiagnosticTool(
        "stat_path", DIAG_FILES,
        "Whether a file or folder exists inside a media folder, with its size, modified time and "
        "extension. Never reads contents.",
        _schema({"media_dir_id": _MEDIA_DIR_ID, "relative_path": _RELATIVE_PATH}, ["media_dir_id", "relative_path"]),
    ),
    DiagnosticTool(
        "list_dir", DIAG_FILES,
        "Names of the folders and files in a folder inside a media folder, with totals.",
        _schema({
            "media_dir_id": _MEDIA_DIR_ID, "relative_path": _RELATIVE_PATH,
            "limit": {"type": "integer", "minimum": 1, "maximum": 200},
        }, ["media_dir_id"]),
    ),
    # ---- jobs ----
    DiagnosticTool(
        "worker_status", DIAG_JOBS,
        "Whether the background task host is running (from its heartbeat), its workers alive and "
        "busy, queued/running task counts, and the last queue activity.",
    ),
    DiagnosticTool(
        "recent_jobs", DIAG_JOBS,
        "Recent background jobs (indexing, syncs, automation runs): name, status, counts, error, times.",
        _schema({
            "status": {"type": "string", "enum": ["PENDING", "RUNNING", "COMPLETED", "CANCELLED", "FAILED"]},
            "limit": {"type": "integer", "minimum": 1, "maximum": MAX_JOBS},
        }),
    ),
    DiagnosticTool(
        "job_detail", DIAG_JOBS,
        "One job with its automation (if any) and its queued tasks: status, attempts, error, and a "
        "summary of their arguments.",
        _schema({"job_id": {"type": "string"}}, ["job_id"]),
    ),
    DiagnosticTool(
        "failed_tasks", DIAG_JOBS,
        "Background tasks that failed, grouped by task name and error.",
        _schema({
            "name": {"type": "string", "description": "Only this task name."},
            "since_hours": {"type": "integer", "minimum": 1, "maximum": 720, "description": "Default 24."},
        }),
    ),
    DiagnosticTool(
        "automation_runs", DIAG_JOBS,
        "An automation's recent runs: status, message, error, and times.",
        _schema({
            "slug": {"type": "string", "description": "The automation's slug from settings_summary."},
            "limit": {"type": "integer", "minimum": 1, "maximum": MAX_JOBS},
        }, ["slug"]),
    ),
)

TOOLS_BY_NAME = {tool.name: tool for tool in TOOLS}


def tool_names(groups: frozenset[str]) -> list[str]:
    """The diagnostic tools offered for these enabled groups (none when all are off)."""
    if not groups:
        return []
    return [tool.name for tool in TOOLS if tool.group == OVERVIEW or tool.group in groups]


def _iso(value: Optional[datetime]) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S") if value else "-"


def _epoch(value: Optional[float]) -> str:
    return datetime.fromtimestamp(value).strftime("%Y-%m-%d %H:%M:%S") if value else "-"


def _first_line(value: Optional[str], limit: int = 300) -> str:
    line = (value or "").strip().splitlines()[0] if (value or "").strip() else ""
    return line if len(line) <= limit else line[:limit] + "…"


def _summarize_arg(value: Any) -> str:
    if isinstance(value, (list, tuple)):
        return f"{len(value)} item(s)"
    if isinstance(value, dict):
        return f"{{{', '.join(list(value)[:5])}}}"
    rendered = repr(value)
    return rendered if len(rendered) <= 40 else rendered[:40] + "…"


def _plausible_year_bounds() -> tuple[int, int]:
    return 1900, datetime.now().year + 1


class DiagnosticsToolProvider(ToolProvider):
    def __init__(
        self,
        session: Session,
        *,
        groups: frozenset[str],
        redactor: Optional[Redactor] = None,
        fs: Optional[AssistantFS] = None,
        store: Optional[Store] = None,
        model_label: str = "",
        db_path: Optional[Path] = None,
    ):
        self.session = session
        self.groups = groups
        self.redactor = redactor or Redactor()
        self._fs = fs
        self._store = store
        self.model_label = model_label
        self._db_path = db_path

    # ---- wiring ---------------------------------------------------------

    @property
    def fs(self) -> AssistantFS:
        if self._fs is None:
            self._fs = AssistantFS({m.id: m.path for m in media_dir_repository.get_media_dir_entries(self.session)})
        return self._fs

    @property
    def store(self) -> Store:
        if self._store is None:
            from yaffo.background_tasks.config import task_queue
            self._store = task_queue.store
        return self._store

    @property
    def db_path(self) -> Optional[Path]:
        if self._db_path is not None:
            return self._db_path
        database = self.session.get_bind().engine.url.database
        return Path(database) if database else None

    def get_tools(self) -> list[RawToolDefinition]:
        return [
            RawToolDefinition(tool.name, tool.description, tool.schema)
            for tool in TOOLS if tool.name in tool_names(self.groups)
        ]

    def call_tool(self, name: str, args: dict) -> CallToolReturn:
        if name not in tool_names(self.groups):
            raise ValueError(f"Unknown or disabled tool: {name}")
        handler: Callable[[dict], Output] = getattr(self, f"_{name}")
        try:
            output = handler(args or {})
        except FsError as exc:
            output = Output(f"Could not complete the check: {exc}.", error=True)
        text = truncate_tool_result(self.redactor(output.text), MAX_RESULT_CHARS)
        activity = ToolActivity(
            tool=name, args=_activity_args(args or {}), count=output.count, detail=text, error=output.error)
        return ToolResult(
            model_text=f"<data source=\"{name}\">\n{text}\n</data>",
            host_data=activity.to_dict(),
        )

    # ---- overview -------------------------------------------------------

    def _health_report(self, args: dict) -> Output:
        findings = self.health_findings()
        worst = health.overall(findings)
        lines = [f"Overall: {worst}"]
        for finding in sorted(findings, key=lambda f: health.LEVELS.index(f.level), reverse=True):
            doc = f" (see {finding.doc})" if finding.doc and finding.level != health.OK else ""
            lines.append(f"[{finding.level}] {finding.message}{doc}")
        return Output("\n".join(lines), count=sum(1 for f in findings if f.level != health.OK))

    def health_findings(self) -> list[health.Finding]:
        findings: list[health.Finding] = []
        if DIAG_FILES in self.groups:
            entries = media_dir_repository.get_media_dir_entries(self.session)
            findings += health.check_no_media_dirs(len(entries))
            for entry in entries:
                facts = self._media_dir_facts(entry.id)
                probe = None
                if facts.get("exists"):
                    probe = self.fs.probe(entry.id).__dict__
                findings += health.check_media_dir(str(entry.path), facts, probe)
            findings += health.check_thumbnail_dir(**self._thumbnail_facts(count_files=False))
        if DIAG_LIBRARY in self.groups:
            faces = self._face_counts()
            findings += health.check_faces(
                faces["linked_not_assigned"], faces["processing"], self._face_tasks_queued(), faces["ignored_linked"])
            stats = self._date_counts()
            findings += health.check_dates(stats["implausible"], stats["undated"], stats["total"])
        if DIAG_JOBS in self.groups:
            heartbeat = self.store.read_heartbeat()
            running = self.session.query(func.count(Job.id)).filter(Job.status == JOB_STATUS_RUNNING).scalar() or 0
            age = time.time() - heartbeat.beat_at if heartbeat else None
            findings += health.check_worker(age, running)
            watcher = self.fs.process_status("watcher")
            if not watcher or not watcher["healthy"] or time.time() - watcher["beat_at"] > health.HEARTBEAT_STALE_SECONDS:
                findings.append(health.Finding("watcher", health.WARNING,
                    "The file watcher has no recent healthy heartbeat; automatic file changes may not be indexed.",
                    health.DOC_JOBS))
            failed = self.store.failed_tasks(time.time() - 86400)
            findings += health.check_failed_tasks(dict(Counter(row["name"] for row in failed)))
            findings += health.check_automations(self._automation_outcomes())
        findings += health.check_migrations(self._pending_migrations())
        findings += health.check_tools(**self._tool_facts())
        heartbeat = self.store.read_heartbeat()
        findings += health.check_install(
            heartbeat.started_at if heartbeat else None, _code_last_changed(Path(yaffo.__file__).resolve().parent))
        return findings

    def _install_info(self, args: dict) -> Output:
        from yaffo.version import get_build_info
        build = get_build_info()
        package_dir = Path(yaffo.__file__).resolve().parent
        lines = [
            f"Version: {build.version}" + (f" (built {build.build_time})" if build.build_time else " (dev build)"),
            f"Install kind: {_install_kind(package_dir)}",
            f"Code location: {package_dir}",
        ]
        changed = _code_last_changed(package_dir)
        if changed:
            lines.append(f"Code last changed: {_epoch(changed)}")
        heartbeat = self.store.read_heartbeat()
        if heartbeat:
            lines.append(f"Task host started: {_epoch(heartbeat.started_at)}")
            if changed and heartbeat.started_at < changed:
                lines.append("Note: the task host started before the code last changed, so it may be running old code.")
        else:
            lines.append("Task host: never reported in")
        web = self.fs.process_status("web")
        if web:
            lines.append(f"Web server started: {_epoch(web['started_at'])}")
            if time.time() - web['beat_at'] > health.HEARTBEAT_STALE_SECONDS:
                lines.append("Web server: NOT RESPONDING (stale heartbeat)")
            if changed and web['started_at'] < changed:
                lines.append("Note: the web server started before the code last changed; restart it to load changes.")
        else:
            lines.append("Web server: no status available")
        lines += [
            f"Python: {platform.python_version()}",
            f"Platform: {platform.platform()}",
        ]
        if self.model_label:
            lines.append(f"AI: {self.model_label}")
        return Output("\n".join(lines))

    def _settings_summary(self, args: dict) -> Output:
        lines = ["Media folders:"]
        entries = media_dir_repository.get_media_dir_entries(self.session)
        lines += [f"- id {e.id}: {e.path}" for e in entries] or ["- (none)"]
        thumbnail_dir = get_thumbnail_dir(self.session)
        lines.append(f"Thumbnail folder: {thumbnail_dir or '(not set)'}")
        lines.append(f"Language: {get_saved_locale(self.session) or '(default)'}")
        automations = self.session.query(Automation).filter(Automation.enabled.is_(True)).order_by(Automation.name).all()
        lines.append("Enabled automations: " + (
            ", ".join(f"{a.display_name} (slug {a.slug})" for a in automations) or "(none)"))
        lines.append("config.toml:")
        for section, values in sorted(app_config.as_dict().items()):
            for key, value in sorted(values.items()):
                if _CONFIG_SECRET.search(key):
                    continue
                lines.append(f"- [{section}] {key} = {value!r}")
        return Output("\n".join(lines))

    def _migration_status(self, args: dict) -> Output:
        bundled = bundled_migrations()
        applied = self._applied_migrations()
        if applied is None:
            return Output(f"{len(bundled)} migrations ship with this build; the applied list isn't recorded in this database.")
        pending = self._pending_migrations()
        lines = [f"Shipped: {len(bundled)}; applied: {len(applied)}; pending: {len(pending)}"]
        lines += [f"- pending: {name}" for name in pending]
        if bundled:
            lines.append(f"Latest shipped: {bundled[-1][1]}")
        return Output("\n".join(lines), count=len(pending))

    def _applied_migrations(self) -> Optional[set[int]]:
        try:
            names = [row[0] for row in self.session.execute(text("SELECT name FROM schema_migrations"))]
        except Exception:
            self.session.rollback()
            return None
        return {n for n in (migration_number(name) for name in names) if n is not None}

    def _pending_migrations(self) -> list[str]:
        applied = self._applied_migrations()
        if applied is None:
            return []
        return [name for number, name in bundled_migrations() if number not in applied]

    DiagnosticTool(
        "capture_date_source", DIAG_METADATA,
        "Re-read only capture-date metadata for one indexed item. Compare the current EXIF or filename "
        "candidate with the stored date; this does not prove its historical source. No pixels or writes.",
        _schema({"media_item_id": {"type": "integer"}}, ["media_item_id"]),
    ),
    # ---- library --------------------------------------------------------

    def _library_stats(self, args: dict) -> Output:
        s = self.session
        by_type = dict(s.query(MediaItem.media_type, func.count(MediaItem.id)).group_by(MediaItem.media_type).all())
        by_status = dict(s.query(MediaItem.status, func.count(MediaItem.id)).group_by(MediaItem.status).all())
        faces = dict(s.query(Face.status, func.count(Face.id)).group_by(Face.status).all())
        low, high = _plausible_year_bounds()
        plausible = MediaItem.year.between(low, high)
        first, last = s.query(func.min(MediaItem.date_taken), func.max(MediaItem.date_taken)).filter(plausible).one()
        dates = self._date_counts()
        lines = [
            f"Media items: {dates['total']} ({_counts(by_type)})",
            f"Index status: {_counts(by_status)}",
            f"Faces: {sum(faces.values())} ({_counts(faces)})",
            f"People: {s.query(func.count(Person.id)).scalar() or 0}",
            f"Distinct tags: {s.query(func.count(func.distinct(Tag.tag_name))).scalar() or 0}",
            f"Albums: {s.query(func.count(Album.id)).scalar() or 0}",
            f"Date range: {first or '-'} to {last or '-'}",
            f"Undated: {dates['undated']}",
            f"Impossible dates: {dates['implausible']}",
        ]
        return Output("\n".join(lines), count=dates["total"])

    def _date_counts(self) -> dict[str, int]:
        low, high = _plausible_year_bounds()
        s = self.session
        return {
            "total": s.query(func.count(MediaItem.id)).scalar() or 0,
            "undated": s.query(func.count(MediaItem.id)).filter(MediaItem.date_taken.is_(None)).scalar() or 0,
            "implausible": s.query(func.count(MediaItem.id)).filter(
                MediaItem.date_taken.isnot(None), or_(MediaItem.year < low, MediaItem.year > high)).scalar() or 0,
        }

    def _capture_date_source(self, args: dict) -> Output:
        item = self.session.get(MediaItem, int(args.get("media_item_id") or 0))
        if item is None:
            return Output("No such media item.", error=True)
        location = self._relative_location(item.full_file_path)
        if not location:
            return Output("The item is outside the configured media folders; metadata was not read.", error=True)
        candidate = self.fs.capture_date_source(*location)
        return Output(f"Stored date: {item.date_taken or '(none)'}\nCurrent date candidate: "
                      + json.dumps(candidate) + "\nThis is a re-read, not proof of the original indexing source.", count=1)

    def _media_item_report(self, args: dict) -> Output:
        media_item_id = int(args.get("media_item_id") or 0)
        item = self.session.get(MediaItem, media_item_id)
        if item is None:
            return Output(f"No media item with id {media_item_id}.", error=True)
        location = self._relative_location(item.full_file_path)
        lines = [f"Media item {item.id} ({item.media_type})"]
        if location:
            dir_id, relative = location
            lines.append(f"Location: media folder {dir_id}, {relative}")
            try:
                stat = self.fs.stat(dir_id, relative)
                lines.append(f"File exists: {'yes' if stat.exists else 'NO'}" + (
                    f", {stat.size} bytes, modified {_epoch(stat.modified)}" if stat.exists else ""))
            except FsError as exc:
                lines.append(f"File check: {exc}")
        else:
            lines.append(f"Location: {Path(item.full_file_path or '').name} (not inside any configured media folder)")
        lines += [
            f"Index status: {item.status}",
            f"Date taken: {item.date_taken or '(none)'}",
            f"Device: {item.device or '-'}",
            f"Location name: {item.location_name or '-'}; has GPS: {'yes' if item.latitude is not None else 'no'}",
            f"Favorite: {bool(item.favorite)}",
        ]
        if DIAG_METADATA in self.groups:
            try:
                lines.append(self._capture_date_source({"media_item_id": item.id}).text)
            except FsError as exc:
                lines.append(f"Capture-date metadata: {exc}")
        faces = self.session.query(Face).filter(Face.media_item_id == item.id).order_by(Face.id).all()
        lines.append(f"Faces: {len(faces)}")
        for face in faces:
            person = face.person_face.person if face.person_face is not None else None
            crop = "crop ok" if face.full_file_path and _exists(face.full_file_path) else "crop MISSING"
            owner = f"person {person.id} ({person.name})" if person else "no person"
            lines.append(f"- face {face.id}: {face.status}, {owner}, {crop}")
        tags = self.session.query(Tag).filter(Tag.media_item_id == item.id).all()
        lines.append("Tags: " + (", ".join(t.tag_name + (f"={t.tag_value}" if t.tag_value else "") for t in tags) or "-"))
        albums = (self.session.query(Album.name).join(AlbumItem, AlbumItem.album_id == Album.id)
                  .filter(AlbumItem.media_item_id == item.id).all())
        lines.append("Albums: " + (", ".join(a[0] for a in albums) or "-"))
        if item.poster_path:
            lines.append(f"Video poster: {'ok' if _exists(item.poster_path) else 'MISSING'}")
        return Output("\n".join(lines), count=1)

    def _relative_location(self, full_path: Optional[str]) -> Optional[tuple[str, str]]:
        if not full_path:
            return None
        path = Path(full_path)
        for entry in media_dir_repository.get_media_dir_entries(self.session):
            try:
                return entry.id, path.relative_to(entry.path).as_posix()
            except ValueError:
                continue
        return None

    def _face_counts(self, samples: bool = False) -> dict:
        s = self.session
        linked_not_assigned = s.query(Face.id).join(PersonFace, PersonFace.face_id == Face.id).filter(
            or_(Face.status.is_(None), Face.status != FACE_STATUS_ASSIGNED))
        processing = s.query(Face.id).filter(Face.status == FACE_STATUS_PROCESSING)
        ignored_linked = s.query(Face.id).join(PersonFace, PersonFace.face_id == Face.id).filter(
            Face.status == FACE_STATUS_IGNORED)
        counts = {
            "linked_not_assigned": linked_not_assigned.count(),
            "processing": processing.count(),
            "ignored_linked": ignored_linked.count(),
        }
        if samples:
            counts["samples"] = {
                "linked_not_assigned": [r[0] for r in linked_not_assigned.limit(MAX_SAMPLE_IDS)],
                "processing": [r[0] for r in processing.limit(MAX_SAMPLE_IDS)],
                "ignored_linked": [r[0] for r in ignored_linked.limit(MAX_SAMPLE_IDS)],
            }
        return counts

    def _face_tasks_queued(self) -> int:
        counts = self.store.status_counts(FACE_TASK_NAMES)
        return counts.get(STATUS_READY, 0) + counts.get(STATUS_RUNNING, 0)

    def _face_consistency(self, args: dict) -> Output:
        counts = self._face_counts(samples=True)
        queued = self._face_tasks_queued()
        samples = counts["samples"]
        lines = [
            f"Linked to a person but not ASSIGNED: {counts['linked_not_assigned']}"
            + (f" (e.g. face ids {samples['linked_not_assigned']})" if samples["linked_not_assigned"] else ""),
            f"PROCESSING: {counts['processing']} with {queued} face task(s) queued or running"
            + (f" (e.g. face ids {samples['processing']})" if samples["processing"] else ""),
            f"IGNORED but still linked: {counts['ignored_linked']}"
            + (f" (e.g. face ids {samples['ignored_linked']})" if samples["ignored_linked"] else ""),
        ]
        problems = counts["linked_not_assigned"] + counts["ignored_linked"] + (counts["processing"] if not queued else 0)
        return Output("\n".join(lines), count=problems)

    def _date_outliers(self, args: dict) -> Output:
        limit = max(1, min(int(args.get("limit") or 20), MAX_OUTLIERS))
        low, high = _plausible_year_bounds()
        query = self.session.query(MediaItem.id, MediaItem.full_file_path, MediaItem.date_taken).filter(
            MediaItem.date_taken.isnot(None), or_(MediaItem.year < low, MediaItem.year > high))
        total = query.count()
        rows = query.order_by(MediaItem.id).limit(limit).all()
        if not rows:
            return Output("No photos have impossible dates.")
        lines = [f"{total} item(s) with dates outside {low}–{high}:"]
        lines += [f"- id {r.id}: {Path(r.full_file_path or '').name}, date {r.date_taken}" for r in rows]
        return Output("\n".join(lines), count=total)

    def _db_quick_check(self, args: dict) -> Output:
        path = self.db_path
        if path is None or not path.exists():
            return Output("The library database file couldn't be located.", error=True)
        deadline = time.monotonic() + QUICK_CHECK_SECONDS
        conn = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True, timeout=5)
        try:
            conn.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 10000)
            try:
                rows = [row[0] for row in conn.execute("PRAGMA quick_check(20)")]
            except sqlite3.OperationalError as exc:
                if "interrupt" in str(exc).lower():
                    return Output(f"The quick check didn't finish within {QUICK_CHECK_SECONDS:g}s.", error=True)
                raise
        finally:
            conn.close()
        wal = path.with_name(path.name + "-wal")
        wal_size = wal.stat().st_size if wal.exists() else 0
        ok = rows == ["ok"]
        lines = [f"Quick check: {'ok' if ok else 'PROBLEMS FOUND'}"]
        if not ok:
            lines += [f"- {row}" for row in rows]
        lines.append(f"Database size: {health.format_size(path.stat().st_size)}; WAL: {health.format_size(wal_size)}")
        return Output("\n".join(lines), count=0 if ok else len(rows))

    # ---- logs -----------------------------------------------------------

    def _recent_errors(self, args: dict) -> Output:
        since_hours = max(1, min(int(args.get("since_hours") or 24), 720))
        limit = max(1, min(int(args.get("limit") or 15), MAX_ERROR_GROUPS))
        since = datetime.now() - timedelta(hours=since_hours)
        groups: dict[tuple[str, str], dict] = {}
        for name in LOG_NAMES:
            if not self.fs.log_files(name):
                continue
            for line in self.fs.read_log_lines(name):
                match = _LOG_LINE.match(line)
                if not match:
                    continue
                stamp_text, _logger, _where, level, message = match.groups()
                try:
                    stamp = datetime.strptime(stamp_text, _LOG_TIME_FORMAT)
                except ValueError:
                    continue
                if stamp < since:
                    continue
                key = (level, _normalize_message(message))
                group = groups.setdefault(key, {
                    "level": level, "log": name, "example": message[:300], "count": 0,
                    "first": stamp, "last": stamp,
                })
                group["count"] += 1
                group["first"] = min(group["first"], stamp)
                group["last"] = max(group["last"], stamp)
                group["example"] = message[:300]
        if not groups:
            return Output(f"No errors or warnings in the last {since_hours} hours.")
        ordered = sorted(groups.values(), key=lambda g: (g["level"] != "ERROR", -g["count"], -g["last"].timestamp()))
        errors = sum(g["count"] for g in groups.values() if g["level"] in ("ERROR", "CRITICAL"))
        lines = [f"{len(groups)} distinct message(s) in the last {since_hours} hours ({errors} error line(s)):"]
        for group in ordered[:limit]:
            lines.append(
                f"- {group['level']} ×{group['count']} in {group['log']} "
                f"(first {_iso(group['first'])}, last {_iso(group['last'])}): {group['example']}"
            )
        if len(ordered) > limit:
            lines.append(f"({len(ordered) - limit} more not shown)")
        return Output("\n".join(lines), count=errors)

    def _read_log(self, args: dict) -> Output:
        name = str(args.get("name") or "")
        tail = self.fs.tail_log(name, int(args.get("tail") or 200), (args.get("contains") or None))
        if not tail.lines:
            return Output(f"{name}: no matching lines.", count=0)
        errors = sum(1 for line in tail.lines if " - ERROR - " in line or " - CRITICAL - " in line)
        header = f"{name}: last {len(tail.lines)} line(s)" + (
            f" containing {args.get('contains')!r} ({tail.matched} matched)" if args.get("contains") else "")
        return Output(header + "\n" + "\n".join(tail.lines), count=errors)

    # ---- files ----------------------------------------------------------

    def _media_dir_facts(self, media_dir_id: str) -> dict:
        try:
            return self.fs.media_dir_facts(media_dir_id)
        except FsError as exc:
            return {"error": str(exc)}

    def _media_dir_status(self, args: dict) -> Output:
        entries = media_dir_repository.get_media_dir_entries(self.session)
        if not entries:
            return Output("No media folders are configured.")
        lines = []
        problems = 0
        for entry in entries:
            facts = self._media_dir_facts(entry.id)
            if facts.get("error"):
                problems += 1
                lines.append(f"- id {entry.id}: {entry.path}: did not respond ({facts['error']})")
                continue
            if not facts.get("exists"):
                problems += 1
                lines.append(f"- id {entry.id}: {entry.path}: MISSING or not mounted")
                continue
            lines.append(
                f"- id {entry.id}: {entry.path}: exists; "
                f"{'separate volume' if facts.get('on_mounted_volume') else 'system volume'}; "
                f"filesystem {facts.get('filesystem_type') or 'unknown'}; "
                f"readable {facts.get('readable')}, writable {facts.get('writable')}; "
                f"{health.format_size(facts.get('free_bytes') or 0)} free of {health.format_size(facts.get('total_bytes') or 0)}"
            )
        return Output("\n".join(lines), count=problems)

    def _probe_media_dir(self, args: dict) -> Output:
        media_dir_id = str(args.get("media_dir_id") or "")
        probe = self.fs.probe(media_dir_id)
        root = self.fs.media_root(media_dir_id)
        if not probe.responded:
            return Output(f"{root}: {probe.error}.", error=False, count=1)
        if probe.error:
            return Output(f"{root}: listing failed after {probe.seconds:.2f}s: {probe.error}.", count=1)
        return Output(f"{root}: responded in {probe.seconds:.2f}s ({probe.entries} entries read).")

    def _thumbnail_facts(self, count_files: bool) -> dict:
        thumbnail_dir = get_thumbnail_dir(self.session)
        facts: dict[str, Any] = {"configured": thumbnail_dir is not None, "exists": False,
                                 "inside_media_dir": False, "has_marker": False}
        if thumbnail_dir is None:
            return facts
        media_dirs = media_dir_repository.get_media_dirs(self.session)

        def gather() -> dict:
            exists = thumbnail_dir.is_dir()
            resolved = thumbnail_dir.resolve() if exists else thumbnail_dir
            inside = any(
                resolved == d.resolve() or d.resolve() in resolved.parents for d in media_dirs if d.exists())
            result = {**facts, "exists": exists, "inside_media_dir": inside,
                      "has_marker": exists and (thumbnail_dir / THUMBNAIL_DIR_MARKER).is_file()}
            if count_files and exists:
                files: list[str] = []
                size = 0
                capped = False
                for dirpath, _dirs, names in os.walk(thumbnail_dir):
                    for name in names:
                        if name == THUMBNAIL_DIR_MARKER:
                            continue
                        full = os.path.join(dirpath, name)
                        files.append(full)
                        try:
                            size += os.path.getsize(full)
                        except OSError:
                            pass
                        if len(files) >= MAX_THUMBNAIL_FILES:
                            capped = True
                            break
                    if capped:
                        break
                result.update({"_files": files, "size": size, "capped": capped})
            return result

        return run_with_timeout(gather, 15.0)

    def _thumbnail_dir_status(self, args: dict) -> Output:
        facts = self._thumbnail_facts(count_files=True)
        if not facts["configured"]:
            return Output("No thumbnail folder is set.")
        thumbnail_dir = get_thumbnail_dir(self.session)
        lines = [f"Thumbnail folder: {thumbnail_dir}", f"Exists: {facts['exists']}"]
        if facts["exists"]:
            lines.append(f"Inside a media folder: {facts['inside_media_dir']}; marker file present: {facts['has_marker']}")
            files = facts.pop("_files", [])
            used = {p for (p,) in self.session.query(Face.full_file_path).filter(Face.full_file_path.isnot(None))}
            used |= {p for (p,) in self.session.query(MediaItem.poster_path).filter(MediaItem.poster_path.isnot(None))}
            orphaned = sum(1 for f in files if f not in used)
            more = "+" if facts.get("capped") else ""
            lines.append(f"Files: {len(files)}{more}, {health.format_size(facts.get('size') or 0)}{more}")
            lines.append(f"Not used by any face or video: {orphaned}{more}")
        problems = sum(1 for f in health.check_thumbnail_dir(
            facts["configured"], facts["exists"], facts["inside_media_dir"], facts["has_marker"]) if f.level != health.OK)
        return Output("\n".join(lines), count=problems)

    def _stat_path(self, args: dict) -> Output:
        media_dir_id = str(args.get("media_dir_id") or "")
        relative = str(args.get("relative_path") or "")
        stat = self.fs.stat(media_dir_id, relative)
        target = relative or "(media folder root)"
        if not stat.exists:
            return Output(f"{target}: does not exist.")
        if stat.is_dir:
            return Output(f"{target}: folder, modified {_epoch(stat.modified)}.", count=1)
        return Output(f"{target}: file, {stat.size} bytes, modified {_epoch(stat.modified)}, extension {stat.extension or '-'}.", count=1)

    def _list_dir(self, args: dict) -> Output:
        media_dir_id = str(args.get("media_dir_id") or "")
        relative = str(args.get("relative_path") or "")
        listing = self.fs.list_dir(media_dir_id, relative, int(args.get("limit") or 100))
        lines = [f"{relative or '(media folder root)'}: {listing.total_directories} folder(s), {listing.total_files} file(s)"]
        lines += [f"[dir] {name}" for name in listing.directories]
        lines += [name for name in listing.files]
        if listing.truncated:
            lines.append("(more entries not shown)")
        if listing.denied:
            lines.append(f"({listing.denied} protected entr{'y' if listing.denied == 1 else 'ies'} hidden)")
        return Output("\n".join(lines), count=listing.total_directories + listing.total_files)

    # ---- jobs -----------------------------------------------------------

    def _ai_call_summary(self, args: dict) -> Output:
        records = self.fs.ai_call_summaries(int(args.get("limit") or 20))
        return Output(json.dumps(records, indent=2) if records else "No model calls recorded yet.", count=len(records))

    def _worker_status(self, args: dict) -> Output:
        heartbeat = self.store.read_heartbeat()
        counts = self.store.status_counts()
        lines = []
        if heartbeat is None:
            lines.append("Task host: has never reported in (not running, or an older version).")
        else:
            age = time.time() - heartbeat.beat_at
            state = "running" if age <= health.HEARTBEAT_STALE_SECONDS else "NOT RESPONDING"
            lines.append(
                f"Task host: {state}; last heartbeat {health.format_duration(age)} ago; "
                f"started {_epoch(heartbeat.started_at)}; workers alive {heartbeat.workers}, busy {heartbeat.busy}")
        watcher = self.fs.process_status("watcher")
        if watcher:
            age = time.time() - watcher["beat_at"]
            state = "running" if watcher["healthy"] and age <= health.HEARTBEAT_STALE_SECONDS else "NOT RESPONDING"
            lines.append(f"File watcher: {state}; last heartbeat {health.format_duration(age)} ago")
        else:
            lines.append("File watcher: no status available (not started or an older version)")
        lines.append("Queue: " + (_counts(counts) or "empty"))
        lines.append(f"Last queue activity: {_epoch(self.store.last_activity())}")
        return Output("\n".join(lines), count=counts.get(STATUS_READY, 0) + counts.get(STATUS_RUNNING, 0))

    def _recent_jobs(self, args: dict) -> Output:
        limit = max(1, min(int(args.get("limit") or 20), MAX_JOBS))
        query = self.session.query(Job)
        if args.get("status"):
            query = query.filter(Job.status == str(args["status"]))
        jobs = query.order_by(Job.created_at.desc()).limit(limit).all()
        if not jobs:
            return Output("No matching jobs.")
        return Output("\n".join(_job_line(job) for job in jobs), count=len(jobs))

    def _job_detail(self, args: dict) -> Output:
        job_id = str(args.get("job_id") or "")
        job = self.session.get(Job, job_id)
        if job is None:
            return Output(f"No job with id {job_id!r}.", error=True)
        lines = [_job_line(job)]
        if job.automation is not None:
            lines.append(f"Automation: {job.automation.display_name} (slug {job.automation.slug})")
        if job.message:
            lines.append(f"Message: {_first_line(job.message)}")
        tasks = self.store.tasks_mentioning(job.id)
        lines.append(f"Queue tasks: {len(tasks)}" + (" (first 50)" if len(tasks) >= 50 else ""))
        for task in tasks[:20]:
            task_args = json.loads(task["args_json"] or "[]")
            summary = ", ".join(_summarize_arg(a) for a in task_args if a != job.id)
            error = f"; error: {_first_line(task['error'], 200)}" if task["error"] else ""
            lines.append(f"- {task['name']}({summary}): {task['status']}, attempts {task['attempts']}{error}")
        return Output("\n".join(lines), count=len(tasks))

    def _failed_tasks(self, args: dict) -> Output:
        since_hours = max(1, min(int(args.get("since_hours") or 24), 720))
        rows = self.store.failed_tasks(time.time() - since_hours * 3600, args.get("name") or None, limit=500)
        if not rows:
            return Output(f"No failed tasks in the last {since_hours} hours.")
        groups: dict[tuple[str, str], dict] = {}
        for row in rows:
            key = (row["name"], _normalize_message(_first_line(row["error"], 200)))
            group = groups.setdefault(key, {"count": 0, "last": row["finished_at"], "error": _first_line(row["error"], 300)})
            group["count"] += 1
        lines = [f"{len(rows)} failed task(s) in the last {since_hours} hours:"]
        for (name, _), group in sorted(groups.items(), key=lambda item: -item[1]["count"]):
            lines.append(f"- {name} ×{group['count']} (last {_epoch(group['last'])}): {group['error']}")
        return Output("\n".join(lines), count=len(rows))

    def _automation_runs(self, args: dict) -> Output:
        slug = str(args.get("slug") or "")
        automation = self.session.query(Automation).filter_by(slug=slug).first()
        if automation is None:
            return Output(f"No automation with slug {slug!r}.", error=True)
        limit = max(1, min(int(args.get("limit") or 10), MAX_JOBS))
        jobs = (self.session.query(Job).filter(Job.automation_id == automation.id)
                .order_by(Job.created_at.desc()).limit(limit).all())
        lines = [f"{automation.display_name}: {'enabled' if automation.enabled else 'disabled'}; {len(jobs)} recent run(s)"]
        lines += [_job_line(job) for job in jobs]
        return Output("\n".join(lines), count=len(jobs))

    def _automation_outcomes(self) -> dict[str, list[str]]:
        outcomes: dict[str, list[str]] = {}
        for automation in self.session.query(Automation).filter(Automation.enabled.is_(True)).all():
            statuses = [s for (s,) in self.session.query(Job.status).filter(Job.automation_id == automation.id)
                        .order_by(Job.created_at.desc()).limit(health.FAILING_AUTOMATION_RUNS)]
            outcomes[automation.display_name] = statuses
        return outcomes

    def _tool_facts(self) -> dict[str, bool]:
        from yaffo.utils import face_analysis, image_classifier
        return {
            "exiftool": is_exiftool_available(),
            "ffmpeg": is_ffmpeg_available(),
            "face_model": face_analysis.get_model_location().path is not None,
            "clip_model": image_classifier.get_model_location().path is not None,
        }


def _activity_args(args: dict) -> dict:
    """The call's arguments as the activity line shows them (short scalars only)."""
    shown = {}
    for key, value in args.items():
        if isinstance(value, (int, float, bool)) or (isinstance(value, str) and len(value) <= 120):
            shown[key] = value
    return shown


def _counts(counts: dict) -> str:
    return ", ".join(f"{key or 'none'} {value}" for key, value in sorted(counts.items(), key=lambda i: str(i[0])))


def _job_line(job: Job) -> str:
    error = f"; error: {_first_line(job.error, 200)}" if job.error else ""
    return (
        f"- job {job.id} {job.name}: {job.status}, {job.completed_count or 0}/{job.task_count or 0} done, "
        f"{job.error_count or 0} error(s); created {_iso(job.created_at)}, finished {_iso(job.completed_at)}{error}"
    )


def _normalize_message(message: str) -> str:
    message = re.sub(r"(['\"]).*?\1", "…", message)
    message = re.sub(r"(/|[A-Za-z]:\\)[^\s:,]+", "<path>", message)
    return re.sub(r"\d+", "N", message)[:200]


def _exists(path: str) -> bool:
    try:
        return run_with_timeout(lambda: Path(path).exists(), 2.0)
    except FsError:
        return False


def _install_kind(package_dir: Path) -> str:
    if getattr(sys, "frozen", False):
        return "app bundle"
    if "pipx" in sys.prefix:
        return "pipx"
    if (package_dir.parent / ".git").exists():
        return "dev checkout"
    return "pip install"


def _code_last_changed(package_dir: Path) -> Optional[float]:
    if getattr(sys, "frozen", False):
        return None
    try:
        return max((p.stat().st_mtime for p in package_dir.rglob("*.py")), default=None)
    except OSError:
        return None
