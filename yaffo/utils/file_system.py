"""Server-side directory listing for the in-app folder/file picker.

Yaffo is a local desktop app (the Flask server and the browser run on the same
machine), so the picker browses the *server's* filesystem — which is the user's own
disk. Unlike a native OS dialog this works identically on macOS, Windows, and Linux
(and even headless), with no external dependency. The browser-side modal
(static/components/folder_picker.js) calls `/api/fs/list` to walk directories and
returns the chosen absolute path.
"""
from __future__ import annotations

import platform
import string
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class DirEntry:
    name: str
    path: str
    is_dir: bool


@dataclass
class DirListing:
    path: str                         # the absolute directory being listed
    parent: Optional[str]             # parent dir, or None at a filesystem root
    entries: list[DirEntry]           # sub-folders (+ files when mode allows files)
    roots: list[DirEntry] = field(default_factory=list)  # quick shortcuts (home, drives)
    error: str = ""


def _drive_roots() -> list[DirEntry]:
    """Windows drive letters that currently exist (C:\\, D:\\, ...). Empty elsewhere."""
    roots: list[DirEntry] = []
    for letter in string.ascii_uppercase:
        drive = Path(f"{letter}:\\")
        if drive.exists():
            roots.append(DirEntry(name=f"{letter}:", path=str(drive), is_dir=True))
    return roots


def _external_volume_roots(volumes_dir: Path | None = None) -> list[DirEntry]:
    """Mounted macOS volumes. Empty on platforms without /Volumes."""
    if volumes_dir is None and platform.system() != "Darwin":
        return []
    volumes = volumes_dir or Path("/Volumes")
    if not volumes.is_dir():
        return []
    roots: list[DirEntry] = []
    try:
        children = sorted(volumes.iterdir(), key=lambda p: p.name.lower())
    except OSError:
        return []

    try:
        system_root = Path("/").resolve()
    except OSError:
        system_root = Path("/")
    for child in children:
        if child.name.startswith("."):
            continue
        try:
            if not child.is_dir():
                continue
            if child.resolve() == system_root:
                continue
        except OSError:
            continue
        roots.append(DirEntry(name=child.name, path=str(child), is_dir=True))
    return roots


def _shortcuts() -> list[DirEntry]:
    """Sensible starting points: home, mounted volumes, and Windows drives."""
    home = Path.home()
    shortcuts = [DirEntry(name="Home", path=str(home), is_dir=True)]
    shortcuts.extend(_external_volume_roots())
    shortcuts.extend(_drive_roots())
    return shortcuts


def _resolve_start(path: Optional[str]) -> Path:
    """The directory to list: the requested path if it's an existing dir, else home."""
    if path:
        candidate = Path(path).expanduser()
        if candidate.is_dir():
            return candidate
    return Path.home()


def list_directory(path: Optional[str] = None, mode: str = "folder") -> DirListing:
    """List `path` (or the home dir if it's missing/not a directory) for the picker.
    Always returns sub-folders; in "file" and "any" modes also returns files.
    Hidden entries (dot-prefixed) are skipped. Never raises — permission/IO
    problems come back in `error` with whatever could still be listed."""
    if mode not in ("folder", "file", "any"):
        mode = "folder"
    directory = _resolve_start(path)
    parent = str(directory.parent) if directory.parent != directory else None

    entries: list[DirEntry] = []
    error = ""
    try:
        for child in sorted(directory.iterdir(), key=lambda p: p.name.lower()):
            if child.name.startswith("."):
                continue
            try:
                is_dir = child.is_dir()
            except OSError:
                continue  # broken symlink / unreadable — skip it
            if is_dir or mode in ("file", "any"):
                entries.append(DirEntry(name=child.name, path=str(child), is_dir=is_dir))
    except PermissionError:
        error = f"Permission denied: {directory}"
    except OSError as e:
        error = f"Could not read {directory}: {e}"

    return DirListing(
        path=str(directory),
        parent=parent,
        entries=entries,
        roots=_shortcuts(),
        error=error,
    )


def listing_to_dict(listing: DirListing) -> dict:
    """Wire shape for `/api/fs/list` (the browser-facing JSON contract)."""
    return {
        "path": listing.path,
        "parent": listing.parent,
        "error": listing.error,
        "entries": [{"name": e.name, "path": e.path, "is_dir": e.is_dir} for e in listing.entries],
        "roots": [{"name": e.name, "path": e.path, "is_dir": e.is_dir} for e in listing.roots],
    }
