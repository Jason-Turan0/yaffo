"""Marks a directory as a yaffo thumbnail dir so indexing never treats it as media.

Skipping only the *configured* thumbnail dir is not enough: another instance's
thumbnail dir (a peer pointed inside this library), a previous thumbnail dir, or the
new dir mid-move (files land before the setting is saved, and the watcher caches the
old setting) all sit in the library holding face crops and posters that would
otherwise be indexed as photos. A marker file travels with the directory itself, so
every scanner can recognise it without knowing any instance's settings.
"""
from pathlib import Path

from yaffo.logging_config import get_logger

logger = get_logger(__name__)

THUMBNAIL_DIR_MARKER = ".yaffo-thumbnails"

_MARKER_CONTENT = (
    "This directory holds yaffo face crops and video posters.\n"
    "yaffo skips any directory containing this file when indexing media.\n"
)


def ensure_thumbnail_dir(thumbnail_dir: Path) -> None:
    """Create `thumbnail_dir` if needed and drop the marker in it.

    Best-effort for the marker: a read-only or odd filesystem must not stop
    indexing, it just falls back to the configured-dir skip."""
    thumbnail_dir.mkdir(parents=True, exist_ok=True)
    marker = thumbnail_dir / THUMBNAIL_DIR_MARKER
    if marker.exists():
        return
    try:
        marker.write_text(_MARKER_CONTENT, encoding="utf-8")
    except OSError as e:
        logger.warning(f"Could not write thumbnail dir marker {marker}: {e}")


def in_marked_thumbnail_dir(path: Path, cache: dict[Path, bool] | None = None) -> bool:
    """True when `path` sits inside a directory carrying the thumbnail marker.

    Checks every ancestor directory. Pass one `cache` dict for the length of a
    directory walk so each directory is stat'ed once; omit it for one-off checks
    (the watcher), where a marker added later must be seen immediately."""
    return _directory_marked(path.parent, cache)


def _directory_marked(directory: Path, cache: dict[Path, bool] | None) -> bool:
    """Whether `directory` or any of its ancestors holds the marker (cached per
    directory as that whole-chain answer, so a hit never skips an ancestor)."""
    if cache is not None and directory in cache:
        return cache[directory]
    marked = (directory / THUMBNAIL_DIR_MARKER).is_file() or (
        directory.parent != directory and _directory_marked(directory.parent, cache)
    )
    if cache is not None:
        cache[directory] = marked
    return marked
