"""Built-in target: keep a copy of the selected photos in a folder outside the
library (a desktop sync app's folder, a NAS share, a frame's SD card).

Each copy's path relative to the folder is its `remote_id`; its size and modification
time are recorded so removal only ever deletes Yaffo's own, unchanged copies (to the
OS trash). See docs/development/automation-sync-adapters.md, *Targets that aren't
adapters › Folder*.
"""
from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any

import send2trash

from yaffo.logging_config import get_logger
from yaffo.sync.convert import JPEG_MIME, Accepts
from yaffo.sync.files import FileStat, is_within, place_copy, sanitize_name
from yaffo.sync.handlers import (
    ERROR_CHANGED, ERROR_NOT_FOUND, ERROR_REJECTED, ERROR_RETRY_LATER, MISSING, PRESENT,
    TARGETS_MANY, HookContext, TargetCapabilities, TargetHandler,
)
from yaffo.sync.options import OPTION_LOCAL_FOLDER, OPTION_SELECT
from yaffo.sync.schemas import PAUSE_DESTINATION_MISSING
from yaffo.utils.photo_dates import parse_date_taken

logger = get_logger(__name__, 'background_tasks')

LAYOUT_PER_TARGET = "per_target"
LAYOUT_FLAT = "flat"
LAYOUT_BY_DATE = "by_date"

CONVERT_OFF = "off"
CONVERT_JPEG = "jpeg"
CONVERT_FRAME = "frame"

ON_LEAVE_DELETE = "delete"
ON_LEAVE_KEEP = "keep"

UNDATED_FOLDER = "Undated"

# "Sized for a photo frame": comfortably above common frame panels (1280x800 to
# 2560x1600) while keeping files small enough for SD cards and slow frame CPUs.
FRAME_MAX_PIXELS = 2560 * 1600
FRAME_MAX_BYTES = 3_000_000

OPTIONS: dict[str, dict[str, Any]] = {
    "folder": {"type": OPTION_LOCAL_FOLDER, "label": "Folder", "required": True,
               "help": "Copies go here. It must exist, and it can't be inside your library."},
    "layout": {"type": OPTION_SELECT, "label": "Layout", "default": LAYOUT_PER_TARGET, "choices": [
        {"value": LAYOUT_PER_TARGET, "label": "One subfolder per album"},
        {"value": LAYOUT_FLAT, "label": "All in the folder"},
        {"value": LAYOUT_BY_DATE, "label": "By year and month taken"},
    ]},
    "convert": {"type": OPTION_SELECT, "label": "Convert to JPEG", "default": CONVERT_OFF, "choices": [
        {"value": CONVERT_OFF, "label": "No, copy the originals"},
        {"value": CONVERT_JPEG, "label": "Yes"},
        {"value": CONVERT_FRAME, "label": "Yes, sized for a photo frame"},
    ]},
    "on_leave": {"type": OPTION_SELECT, "label": "When a photo leaves the selection",
                 "help": "Remove moves only unchanged copies Yaffo made to the Trash. Keep leaves them at the destination.",
                 "default": ON_LEAVE_DELETE, "choices": [
                     {"value": ON_LEAVE_DELETE, "label": "Remove from destination"},
                     {"value": ON_LEAVE_KEEP, "label": "Keep at destination"},
                 ]},
}


def _accepts(convert: str) -> Accepts | None:
    if convert == CONVERT_JPEG:
        return Accepts(types=(JPEG_MIME, "image/png"))
    if convert == CONVERT_FRAME:
        return Accepts(types=(JPEG_MIME,), max_bytes=FRAME_MAX_BYTES, max_pixels=FRAME_MAX_PIXELS)
    return None


def _capture_time(item: dict[str, Any]) -> datetime | None:
    local = (item.get("taken_at") or {}).get("local")
    try:
        return parse_date_taken(local) if local else None
    except (TypeError, ValueError):
        return None


class FolderTarget(TargetHandler):
    def capabilities(self, options: dict[str, Any]) -> TargetCapabilities:
        return TargetCapabilities(max_batch=50, remove=True, update=False, reconcile=True,
                                  targets=TARGETS_MANY, accepts=_accepts(options.get("convert", CONVERT_OFF)))

    def destination_problem(self, options: dict[str, Any]) -> str | None:
        folder = Path(options["folder"])
        if not folder.is_dir() or not os.access(folder, os.W_OK):
            return PAUSE_DESTINATION_MISSING
        return None

    def describe_destination(self, options: dict[str, Any]) -> str:
        return str(Path(options["folder"]))

    def _subfolder(self, item: dict[str, Any], layout: str) -> PurePosixPath:
        if layout == LAYOUT_FLAT:
            return PurePosixPath()
        if layout == LAYOUT_BY_DATE:
            taken = _capture_time(item)
            return PurePosixPath(f"{taken.year:04d}", f"{taken.month:02d}") if taken else PurePosixPath(UNDATED_FOLDER)
        return PurePosixPath(sanitize_name(item.get("target") or "", fallback="Photos"))

    def push(self, batch: list[dict[str, Any]], ctx: HookContext) -> dict[str, Any]:
        folder = Path(ctx.options["folder"])
        layout = ctx.options.get("layout", LAYOUT_PER_TARGET)
        results = []
        for item in batch:
            source = Path(item["path"])
            if not source.is_file():
                results.append({"key": item["key"], "error": {"code": ERROR_REJECTED,
                                                              "message": "the photo's file is missing"}})
                continue
            subfolder = self._subfolder(item, layout)
            taken = _capture_time(item)
            try:
                written = place_copy(source, folder.joinpath(*subfolder.parts),
                                     sanitize_name(item["filename"]),
                                     mtime=taken.timestamp() if taken else None,
                                     temp_tag=str(ctx.automation_id) if ctx.automation_id else None)
            except OSError as e:
                logger.warning(f"folder target: could not copy {source} into {folder}: {e}")
                results.append({"key": item["key"], "error": {"code": ERROR_RETRY_LATER, "message": str(e)}})
                continue
            stat = FileStat.of(written)
            results.append({
                "key": item["key"],
                "remote_id": written.relative_to(folder).as_posix(),
                "details": {"size": stat.size, "mtime_ns": stat.mtime_ns, "folder": str(folder)},
            })
        return {"results": results}

    def _path_for(self, folder: Path, remote_id: str | None) -> Path | None:
        if not remote_id:
            return None
        path = folder.joinpath(*PurePosixPath(remote_id).parts)
        return path if is_within(path, folder) and path != folder else None

    def remove(self, items: list[dict[str, Any]], ctx: HookContext) -> dict[str, Any]:
        keep = ctx.options.get("on_leave") == ON_LEAVE_KEEP
        results = []
        for item in items:
            folder = Path((item.get("details") or {}).get("folder") or ctx.options["folder"])
            if keep:
                results.append({"key": item["key"], "kept": True})
                continue
            path = self._path_for(folder, item.get("remote_id"))
            if path is None or not path.exists():
                results.append({"key": item["key"], "error": {"code": ERROR_NOT_FOUND}})
                continue
            expected = item.get("details") or {}
            stat = FileStat.of(path)
            if stat.size != expected.get("size") or stat.mtime_ns != expected.get("mtime_ns"):
                results.append({"key": item["key"], "error": {
                    "code": ERROR_CHANGED, "message": "the copy was changed after Yaffo wrote it"}})
                continue
            try:
                send2trash.send2trash(str(path))
            except OSError as e:
                results.append({"key": item["key"], "error": {"code": ERROR_RETRY_LATER, "message": str(e)}})
                continue
            results.append({"key": item["key"]})
        return {"results": results}

    def reconcile(self, items: list[dict[str, Any]], ctx: HookContext) -> dict[str, str]:
        found: dict[str, str] = {}
        for item in items:
            folder = Path((item.get("details") or {}).get("folder") or ctx.options["folder"])
            # A copy the user edited is still there; whether Yaffo may delete it is
            # decided at removal time, not here.
            path = self._path_for(folder, item.get("remote_id"))
            if path is None:
                continue
            found[item["key"]] = PRESENT if path.is_file() else MISSING
        return found
