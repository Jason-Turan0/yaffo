"""Built-in source: copy new photos from a folder (an SD card, a camera, a desktop
sync app's upload folder) into the import root.

One record per file: `source_id` is the path relative to the folder (its folder part is
the record's `folder`, for the keep-the-source's-folders layout), `updated_at` is
size:mtime, and there's no hash in the record; the engine hashes a file only when it
hasn't seen that version before. Originals are never touched, and files disappearing
from the folder is normal, so no tombstones are ever emitted. See
docs/development/automation-sync-adapters.md, *Sources that aren't adapters › Folder*.
"""
from __future__ import annotations

import shutil
from pathlib import Path, PurePosixPath
from typing import Any

from yaffo.common import MEDIA_EXTENSIONS, MEDIA_TYPE_PHOTO, MEDIA_TYPE_VIDEO, media_type_for_path
from yaffo.sync.files import FileStat, is_within
from yaffo.sync.handlers import HookContext, SourceHandler
from yaffo.sync.options import OPTION_BOOL, OPTION_LOCAL_FOLDER, OPTION_SELECT
from yaffo.utils import job_codes as codes
from yaffo.utils.index_photos import is_system_file

PAGE_SIZE = 200

MEDIA_BOTH = "both"
MEDIA_PHOTOS = "photos"
MEDIA_VIDEOS = "videos"


OPTIONS: dict[str, dict[str, Any]] = {
    "folder": {"type": OPTION_LOCAL_FOLDER, "label": "Folder", "required": True,
               "help": "New photos are copied from here. Nothing in it is changed or deleted."},
    "include_subfolders": {"type": OPTION_BOOL, "label": "Include subfolders", "default": True},
    "media": {"type": OPTION_SELECT, "label": "Copy", "default": MEDIA_BOTH, "choices": [
        {"value": MEDIA_BOTH, "label": "Photos and videos"},
        {"value": MEDIA_PHOTOS, "label": "Photos only"},
        {"value": MEDIA_VIDEOS, "label": "Videos only"},
    ]},
}


def _wanted(path: Path, media: str) -> bool:
    if path.suffix.lower() not in MEDIA_EXTENSIONS or is_system_file(path.name) or path.name.startswith("."):
        return False
    kind = media_type_for_path(path)
    return (media == MEDIA_BOTH or (media == MEDIA_PHOTOS and kind == MEDIA_TYPE_PHOTO)
            or (media == MEDIA_VIDEOS and kind == MEDIA_TYPE_VIDEO))


def _hidden(relative: PurePosixPath) -> bool:
    return any(part.startswith(".") for part in relative.parts[:-1])


class FolderSource(SourceHandler):
    def source_problem(self, options: dict[str, Any]) -> str | None:
        return None if Path(options["folder"]).is_dir() else codes.OUTCOME_SYNC_WAITING_FOR_FOLDER

    def describe_source(self, options: dict[str, Any]) -> str:
        return str(Path(options["folder"]))

    def _files(self, folder: Path, options: dict[str, Any]) -> list[tuple[str, Path]]:
        media = options.get("media", MEDIA_BOTH)
        candidates = folder.rglob("*") if options.get("include_subfolders", True) else folder.iterdir()
        found = []
        for path in candidates:
            if not path.is_file() or not _wanted(path, media):
                continue
            relative = PurePosixPath(path.relative_to(folder).as_posix())
            if _hidden(relative):
                continue
            found.append((relative.as_posix(), path))
        found.sort(key=lambda pair: pair[0])
        return found

    def list_page(self, cursor: str | None, ctx: HookContext) -> dict[str, Any]:
        folder = Path(ctx.options["folder"])
        files = self._files(folder, ctx.options)
        remaining = [pair for pair in files if cursor is None or pair[0] > cursor]
        page = remaining[:PAGE_SIZE]
        records = []
        for source_id, path in page:
            try:
                stat = FileStat.of(path)
            except OSError:
                continue
            records.append({
                "source_id": source_id,
                "filename": path.name,
                "folder": PurePosixPath(source_id).parent.as_posix() if "/" in source_id else "",
                "size": stat.size,
                "updated_at": stat.signature,
                "local_path": str(path),
            })
        next_cursor = page[-1][0] if len(remaining) > PAGE_SIZE else None
        return {"records": records, "next_cursor": next_cursor}

    def fetch(self, record: dict[str, Any], destination: Path, ctx: HookContext) -> None:
        folder = Path(ctx.options["folder"])
        source = Path(record["local_path"])
        if not is_within(source, folder):
            raise ValueError(f"{source} is outside the source folder")
        shutil.copy2(source, destination)
