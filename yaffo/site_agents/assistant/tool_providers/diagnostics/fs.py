"""AssistantFS: the assistant's only filesystem access, over named roots.

The model never supplies an absolute path. It names a root and, for media dirs,
a path relative to it:

- `logs`: the two app logs (and their rotated copies), by name, tail only
- `data`: the data directory, stat and free space only
- `media:<id>`: a configured media dir, stat and list names only

Every relative path is resolved inside its root (no `..`, no absolute paths, no
symlink escapes), deny-listed names are refused even inside an allowed root, and
all filesystem work runs on a worker thread with a timeout so a failing external
drive can't hang a turn.
"""
from __future__ import annotations

import fnmatch
import os
import queue
import shutil
import stat as stat_module
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path, PurePath
from typing import Any, Callable, Optional, TypeVar

from yaffo.common import ROOT_DIR
from yaffo.utils.safe_paths import PathOutsideAllowedRoots, resolve_path_in_roots

T = TypeVar("T")

DEFAULT_TIMEOUT_SECONDS = 5.0
MAX_TAIL_LINES = 500
MAX_LINE_CHARS = 400
MAX_LIST_ENTRIES = 200
# Only this much of the end of a log is read to find the last lines.
MAX_TAIL_BYTES = 2 * 1024 * 1024

# Names the model may ask for, mapped to the files they cover (newest first).
LOG_NAMES = ("yaffo.log", "background_tasks.log")
_ROTATED_COPIES = 5

DENIED_NAME_PATTERNS = (
    "*.db", "*.db-wal", "*.db-shm", "*.sqlite", "*.sqlite3",
    "config.toml", ".ssh", ".gnupg", ".aws",
    "*.pem", "*.key", "*.p12", "*.pfx", "id_rsa*", "id_ed25519*", "id_ecdsa*",
    "*credential*", "*secret*", ".env", ".netrc", "*.keychain*",
)


class FsError(Exception):
    """A refused or failed filesystem request, reported to the model as data."""


class FsTimeout(FsError):
    def __init__(self, seconds: float):
        super().__init__(f"did not respond in {seconds:g}s")
        self.seconds = seconds


def is_denied(name: str) -> bool:
    lowered = name.lower()
    return any(fnmatch.fnmatch(lowered, pattern) for pattern in DENIED_NAME_PATTERNS)


def run_with_timeout(func: Callable[[], T], seconds: float = DEFAULT_TIMEOUT_SECONDS) -> T:
    """Run `func` on a daemon thread; raise FsTimeout if it hasn't returned in
    `seconds`. A hung stat keeps its thread, but never the caller."""
    results: "queue.Queue[tuple[bool, Any]]" = queue.Queue(maxsize=1)

    def target() -> None:
        try:
            results.put((True, func()))
        except BaseException as exc:  # handed back to the caller
            results.put((False, exc))

    threading.Thread(target=target, daemon=True, name="assistant-fs").start()
    try:
        ok, value = results.get(timeout=seconds)
    except queue.Empty:
        raise FsTimeout(seconds) from None
    if ok:
        return value
    raise value


@dataclass(frozen=True)
class PathStat:
    exists: bool
    is_dir: bool = False
    size: Optional[int] = None
    modified: Optional[float] = None
    extension: str = ""


@dataclass(frozen=True)
class DirListing:
    directories: list[str]
    files: list[str]
    total_directories: int
    total_files: int
    truncated: bool
    denied: int


@dataclass(frozen=True)
class DiskUsage:
    total: int
    used: int
    free: int


@dataclass(frozen=True)
class Probe:
    responded: bool
    seconds: float
    entries: Optional[int] = None
    error: Optional[str] = None


@dataclass(frozen=True)
class LogTail:
    name: str
    lines: list[str]
    matched: int
    scanned: int


def _relative_parts(relative_path: str) -> list[str]:
    relative_path = (relative_path or "").strip()
    if not relative_path or relative_path in (".", "/"):
        return []
    pure = PurePath(relative_path.replace("\\", "/"))
    if pure.is_absolute() or relative_path.startswith(("/", "\\", "~")) or ":" in pure.parts[0]:
        raise FsError("use a path relative to the media folder, not an absolute path")
    parts = [p for p in pure.parts if p not in ("", ".")]
    if any(p == ".." for p in parts):
        raise FsError("'..' is not allowed in a path")
    for part in parts:
        if is_denied(part):
            raise FsError(f"access to {part!r} is not allowed")
    return parts


class AssistantFS:
    """`media_dirs` maps media dir id → root path; `data_dir` is the app's data dir."""

    def __init__(
        self,
        media_dirs: dict[str, Path],
        *,
        data_dir: Path = ROOT_DIR,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ):
        self._media_dirs = dict(media_dirs)
        self._data_dir = Path(data_dir)
        self.timeout = timeout

    # ---- roots ----------------------------------------------------------

    def media_root(self, media_dir_id: str) -> Path:
        root = self._media_dirs.get(media_dir_id)
        if root is None:
            raise FsError(f"no media folder with id {media_dir_id!r}")
        return root

    def _resolve(self, media_dir_id: str, relative_path: str) -> Path:
        root = self.media_root(media_dir_id)
        parts = _relative_parts(relative_path)
        candidate = root.joinpath(*parts)

        def resolve() -> Path:
            if not root.exists():
                raise FsError("the media folder is not available (missing or not mounted)")
            if not candidate.exists() and not candidate.is_symlink():
                return candidate
            try:
                resolved, _ = resolve_path_in_roots(candidate, [root])
            except PathOutsideAllowedRoots:
                raise FsError("that path leads outside the media folder") from None
            return resolved

        return run_with_timeout(resolve, self.timeout)

    # ---- media dirs -----------------------------------------------------

    def stat(self, media_dir_id: str, relative_path: str) -> PathStat:
        path = self._resolve(media_dir_id, relative_path)

        def do_stat() -> PathStat:
            try:
                info = path.stat()
            except FileNotFoundError:
                return PathStat(exists=False, extension=path.suffix.lower())
            is_dir = stat_module.S_ISDIR(info.st_mode)
            return PathStat(
                exists=True, is_dir=is_dir, size=None if is_dir else info.st_size,
                modified=info.st_mtime, extension="" if is_dir else path.suffix.lower(),
            )

        return run_with_timeout(do_stat, self.timeout)

    def list_dir(self, media_dir_id: str, relative_path: str, limit: int = MAX_LIST_ENTRIES) -> DirListing:
        limit = max(1, min(int(limit or MAX_LIST_ENTRIES), MAX_LIST_ENTRIES))
        path = self._resolve(media_dir_id, relative_path)

        def do_list() -> DirListing:
            if not path.is_dir():
                raise FsError("not a folder" if path.exists() else "no such folder")
            directories: list[str] = []
            files: list[str] = []
            total_dirs = total_files = denied = 0
            with os.scandir(path) as entries:
                for entry in entries:
                    if is_denied(entry.name):
                        denied += 1
                        continue
                    try:
                        is_dir = entry.is_dir(follow_symlinks=False)
                    except OSError:
                        is_dir = False
                    if is_dir:
                        total_dirs += 1
                        if len(directories) + len(files) < limit:
                            directories.append(entry.name)
                    else:
                        total_files += 1
                        if len(directories) + len(files) < limit:
                            files.append(entry.name)
            return DirListing(
                directories=sorted(directories), files=sorted(files),
                total_directories=total_dirs, total_files=total_files,
                truncated=total_dirs + total_files > len(directories) + len(files), denied=denied,
            )

        return run_with_timeout(do_list, self.timeout)

    def probe(self, media_dir_id: str) -> Probe:
        """Time a stat plus a short listing of the root. Never raises for a slow or
        failing drive: that's the answer."""
        root = self.media_root(media_dir_id)
        started = time.monotonic()

        def do_probe() -> int:
            root.stat()
            count = 0
            with os.scandir(root) as entries:
                for _ in entries:
                    count += 1
                    if count >= 50:
                        break
            return count

        try:
            entries = run_with_timeout(do_probe, self.timeout)
        except FsTimeout:
            return Probe(responded=False, seconds=self.timeout, error=f"did not respond in {self.timeout:g}s")
        except OSError as exc:
            return Probe(responded=True, seconds=time.monotonic() - started, error=exc.strerror or str(exc))
        return Probe(responded=True, seconds=time.monotonic() - started, entries=entries)

    def media_dir_facts(self, media_dir_id: str) -> dict:
        """Exists / mounted / readable / writable / free space for one media dir."""
        root = self.media_root(media_dir_id)

        def facts() -> dict:
            exists = root.is_dir()
            result: dict[str, Any] = {"exists": exists}
            if not exists:
                return result
            result["on_mounted_volume"] = _on_mounted_volume(root)
            result["readable"] = os.access(root, os.R_OK)
            result["writable"] = os.access(root, os.W_OK)
            usage = shutil.disk_usage(root)
            result["total_bytes"] = usage.total
            result["free_bytes"] = usage.free
            return result

        return run_with_timeout(facts, self.timeout)

    # ---- data dir -------------------------------------------------------

    def data_dir_usage(self) -> DiskUsage:
        usage = run_with_timeout(lambda: shutil.disk_usage(self._data_dir), self.timeout)
        return DiskUsage(total=usage.total, used=usage.used, free=usage.free)

    # ---- logs -----------------------------------------------------------

    def log_files(self, name: str) -> list[Path]:
        """The log and its rotated copies, newest first (existing files only)."""
        if name not in LOG_NAMES:
            raise FsError(f"unknown log {name!r}; use one of {', '.join(LOG_NAMES)}")
        base = self._data_dir / name
        candidates = [base] + [base.with_name(f"{name}.{i}") for i in range(1, _ROTATED_COPIES + 1)]
        return [p for p in candidates if p.is_file()]

    def tail_log(self, name: str, lines: int = 200, contains: Optional[str] = None) -> LogTail:
        lines = max(1, min(int(lines or 200), MAX_TAIL_LINES))
        files = self.log_files(name)

        def read() -> LogTail:
            kept: deque[str] = deque(maxlen=lines)
            matched = scanned = 0
            # Oldest first, so the deque ends on the newest lines.
            for path in reversed(files if contains else files[:1]):
                for line in _read_tail_lines(path):
                    scanned += 1
                    if contains and contains.lower() not in line.lower():
                        continue
                    matched += 1
                    kept.append(_clip(line))
            return LogTail(name=name, lines=list(kept), matched=matched, scanned=scanned)

        return run_with_timeout(read, self.timeout)

    def read_log_lines(self, name: str) -> list[str]:
        """Every line in the tail window of the log and its rotations, oldest first
        (for grouping errors)."""
        files = self.log_files(name)

        def read() -> list[str]:
            out: list[str] = []
            for path in reversed(files):
                out.extend(_read_tail_lines(path))
            return out

        return run_with_timeout(read, self.timeout)


def _on_mounted_volume(path: Path) -> bool:
    """Whether `path` (or a folder above it, short of the filesystem root) is a
    mount point: an external, network, or separate volume."""
    try:
        for candidate in (path, *path.parents):
            if candidate.parent == candidate:
                return False
            if os.path.ismount(candidate):
                return True
    except OSError:
        pass
    return False


def _clip(line: str) -> str:
    line = line.rstrip("\r\n")
    return line if len(line) <= MAX_LINE_CHARS else line[:MAX_LINE_CHARS] + "…"


def _read_tail_lines(path: Path) -> list[str]:
    """The last MAX_TAIL_BYTES of a text log as lines. Binary content is refused."""
    with path.open("rb") as handle:
        handle.seek(0, os.SEEK_END)
        size = handle.tell()
        start = max(0, size - MAX_TAIL_BYTES)
        handle.seek(start)
        data = handle.read()
    if b"\x00" in data[:8192]:
        raise FsError(f"{path.name} is not a text file")
    text = data.decode("utf-8", errors="replace")
    lines = text.splitlines()
    if start > 0 and lines:
        lines = lines[1:]  # the first line was cut mid-way
    return lines
