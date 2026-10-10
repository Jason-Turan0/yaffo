"""File helpers shared by the sync engine and the built-in Folder source/target.

Every write goes to a temporary `.part` name in the destination folder and is then
renamed into place, so a watcher (Yaffo's own, a desktop sync app, a photo frame)
never sees a half-written file. `.part` isn't a media extension, so Yaffo's watcher
ignores the temporary file.
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path

_HASH_CHUNK = 1024 * 1024
_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED_WINDOWS = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
                     *(f"LPT{i}" for i in range(1, 10))}
MAX_NAME_LENGTH = 180


TEMP_PREFIX = ".yaffo-"
TEMP_SUFFIX = ".part"


def temp_path(folder: Path, tag: str | None = None) -> Path:
    """A temporary name in `folder`. `tag` (the sync's id) lets a later run of the
    same sync find and remove what a crashed run left behind, without touching the
    in-flight files of another sync writing to the same folder."""
    middle = f"{tag}-" if tag else ""
    return folder / f"{TEMP_PREFIX}{middle}{uuid.uuid4().hex}{TEMP_SUFFIX}"


def sweep_temps(folder: Path, tag: str) -> int:
    """Delete this sync's leftover temporary files under `folder`. Call at run start,
    when the sync's previous run (one at a time) can't still be writing them."""
    removed = 0
    if not folder.is_dir():
        return 0
    for leftover in folder.rglob(f"{TEMP_PREFIX}{tag}-*{TEMP_SUFFIX}"):
        try:
            leftover.unlink()
            removed += 1
        except OSError:
            continue
    return removed


def file_sha1(path: Path) -> str:
    digest = hashlib.sha1()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(_HASH_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class FileStat:
    size: int
    mtime_ns: int

    @classmethod
    def of(cls, path: Path) -> FileStat:
        stat = path.stat()
        return cls(size=stat.st_size, mtime_ns=stat.st_mtime_ns)

    @property
    def signature(self) -> str:
        return f"{self.size}:{self.mtime_ns}"


def sanitize_name(name: str, fallback: str = "photo") -> str:
    """A file or folder name that's safe on Windows and macOS: no separators or
    reserved characters, no leading dots (hidden files) or trailing dots/spaces, not a
    reserved Windows device name, and not absurdly long (the extension is kept)."""
    cleaned = _UNSAFE.sub("_", name).strip().lstrip(".").rstrip(". ")
    if not cleaned:
        cleaned = fallback
    stem, dot, suffix = cleaned.rpartition(".")
    if not dot:
        stem, suffix = cleaned, ""
    if stem.upper() in _RESERVED_WINDOWS:
        stem = f"_{stem}"
    extension = f".{suffix}" if suffix else ""
    stem = stem[: max(1, MAX_NAME_LENGTH - len(extension))]
    return f"{stem}{extension}"


def is_within(path: Path, root: Path) -> bool:
    """Whether `path` is `root` or inside it, after resolving `..` and symlinks."""
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def numbered_name(name: str, n: int) -> str:
    """IMG_1234.jpg -> IMG_1234 (2).jpg"""
    stem, dot, suffix = name.rpartition(".")
    if not dot:
        return f"{name} ({n})"
    return f"{stem} ({n}).{suffix}"


def same_content(a: Path, b: Path) -> bool:
    try:
        if a.stat().st_size != b.stat().st_size:
            return False
    except OSError:
        return False
    return file_sha1(a) == file_sha1(b)


def place_copy(source: Path, folder: Path, name: str, mtime: float | None = None,
               temp_tag: str | None = None) -> Path:
    """Copy `source` into `folder` as `name`, atomically, never overwriting a
    different file: a taken name gets " (2)", " (3)", ... An existing file with the
    same content is reused instead of copied again, which makes a repeated copy (a
    retry after a crash) harmless. Returns the final path."""
    folder.mkdir(parents=True, exist_ok=True)
    n = 1
    candidate = folder / name
    while candidate.exists():
        if same_content(source, candidate):
            return candidate
        n += 1
        candidate = folder / numbered_name(name, n)
    temp = temp_path(folder, temp_tag)
    try:
        shutil.copyfile(source, temp)
        if mtime is not None:
            os.utime(temp, (mtime, mtime))
        # A concurrent writer could have taken the name meanwhile; re-check once.
        while candidate.exists():
            n += 1
            candidate = folder / numbered_name(name, n)
        os.replace(temp, candidate)
    finally:
        if temp.exists():
            temp.unlink()
    return candidate


def move_into_place(temp: Path, folder: Path, name: str) -> Path:
    """Rename a finished temporary file into `folder` as `name` (" (2)" etc. when
    taken). A file of that name with the same content is taken to be this one
    already placed (a crashed run's copy): `temp` is dropped and the existing file
    returned."""
    folder.mkdir(parents=True, exist_ok=True)
    n = 1
    candidate = folder / name
    while candidate.exists():
        if same_content(temp, candidate):
            temp.unlink()
            return candidate
        n += 1
        candidate = folder / numbered_name(name, n)
    os.replace(temp, candidate)
    return candidate


def replace_file(source: Path, destination: Path, temp_tag: str | None = None) -> None:
    """Atomically replace `destination`'s content with `source`'s (same folder temp
    then rename). The caller has already moved the old version somewhere safe."""
    temp = temp_path(destination.parent, temp_tag)
    try:
        shutil.copyfile(source, temp)
        os.replace(temp, destination)
    finally:
        if temp.exists():
            temp.unlink()


def free_bytes(folder: Path) -> int:
    return shutil.disk_usage(folder).free
