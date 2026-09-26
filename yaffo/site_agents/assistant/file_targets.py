"""A file or folder the assistant can offer to open on the user's computer, named
by ids only: a media item, or a media folder plus a path inside it. Yaffo looks up
the real path itself, both when the model makes the link (to check it) and when
the user clicks it (to open it), so an absolute path never reaches the model or
the transcript, and nothing outside the configured media folders can be opened."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Optional

from sqlalchemy.orm import Session

from yaffo.db.models import MediaItem
from yaffo.db.repositories.media_dir_repository import get_media_dir_entries
from yaffo.site_agents.assistant.tool_providers.diagnostics.fs import FsError, run_with_timeout
from yaffo.utils.safe_paths import PathOutsideAllowedRoots, resolve_path_in_roots

SHOW_FILE = "file"      # open it with its default app (a folder opens in the file manager)
SHOW_FOLDER = "folder"  # show it in its folder (a folder opens itself)
SHOW_OPTIONS = (SHOW_FILE, SHOW_FOLDER)


class TargetError(ValueError):
    """Why a target can't be opened, in words fit for the model and the user."""


@dataclass(frozen=True)
class FileTarget:
    """What an open link carries: ids, never a path from the model's side."""
    show: str
    media_item_id: Optional[int] = None
    media_dir_id: Optional[str] = None
    path: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Any) -> "FileTarget":
        if not isinstance(data, dict):
            raise TargetError("expected an object")
        show = data.get("show") or SHOW_FILE
        if show not in SHOW_OPTIONS:
            raise TargetError(f"show must be one of {', '.join(SHOW_OPTIONS)}")
        media_item_id = data.get("media_item_id")
        media_dir_id = data.get("media_dir_id")
        path = data.get("path") or ""
        if (media_item_id is None) == (media_dir_id is None):
            raise TargetError("give either media_item_id, or media_dir_id with a path")
        if media_item_id is not None:
            if isinstance(media_item_id, bool) or not isinstance(media_item_id, int):
                raise TargetError("media_item_id must be a number")
            if path:
                raise TargetError("path goes with media_dir_id, not media_item_id")
            return cls(show=show, media_item_id=media_item_id)
        if not isinstance(media_dir_id, str) or not isinstance(path, str):
            raise TargetError("media_dir_id and path must be text")
        return cls(show=show, media_dir_id=media_dir_id, path=path)


@dataclass(frozen=True)
class ResolvedTarget:
    path: Path
    is_dir: bool
    label: str  # how the model and the activity line name it: [media folder <id>]/relative


def _relative_parts(path: str) -> list[str]:
    parts = [p for p in path.replace("\\", "/").split("/") if p not in ("", ".")]
    if path.startswith(("/", "\\")) or (len(path) > 1 and path[1] == ":"):
        raise TargetError("use a path relative to the media folder, not an absolute path")
    if ".." in parts:
        raise TargetError("the path can't go up out of the media folder ('..')")
    return parts


def _label(media_dir_id: str, root: Path, path: Path) -> str:
    relative = path.relative_to(root).as_posix()
    return f"[media folder {media_dir_id}]" + (f"/{relative}" if relative != "." else "")


def resolve_target(session: Session, target: FileTarget) -> ResolvedTarget:
    """The real, existing path inside a configured media folder, or TargetError."""
    roots = {entry.id: entry.path for entry in get_media_dir_entries(session)}
    if target.media_item_id is not None:
        item = session.get(MediaItem, target.media_item_id)
        if item is None or not item.full_file_path:
            raise TargetError(f"no photo or video with id {target.media_item_id}")
        candidate = Path(item.full_file_path)
        allowed = roots
    else:
        root = roots.get(target.media_dir_id or "")
        if root is None:
            raise TargetError(f"no media folder with id {target.media_dir_id!r}")
        candidate = root.joinpath(*_relative_parts(target.path))
        allowed = {target.media_dir_id: root}

    def resolve() -> ResolvedTarget:
        for media_dir_id, root in allowed.items():
            try:
                resolved, resolved_root = resolve_path_in_roots(candidate, [root])
            except PathOutsideAllowedRoots:
                continue
            return ResolvedTarget(resolved, resolved.is_dir(), _label(media_dir_id, resolved_root, resolved))
        if not candidate.exists():
            raise TargetError("that file or folder doesn't exist (or its drive isn't connected)")
        raise TargetError("that isn't inside a configured media folder")

    try:
        return run_with_timeout(resolve)
    except FsError as exc:  # the drive didn't answer in time
        raise TargetError(f"the drive {exc}") from None
