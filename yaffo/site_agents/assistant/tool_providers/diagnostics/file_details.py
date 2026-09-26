"""Fixed, read-only probes for volume type and capture-date metadata."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import json
import os
import platform
import plistlib
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Optional

from yaffo.utils.exiftool_path import get_exiftool_path
from yaffo.utils.photo_dates import MAX_YEAR, MIN_YEAR, get_date_from_filename


def filesystem_type(path: Path) -> Optional[str]:
    try:
        system = platform.system()
        if system == "Darwin":
            resolved = path.resolve()
            volume = next((p for p in (resolved, *resolved.parents) if os.path.ismount(p)), Path(resolved.anchor))
            result = subprocess.run(["/usr/sbin/diskutil", "info", "-plist", str(volume)],
                                    capture_output=True, timeout=3, check=True)
            facts = plistlib.loads(result.stdout)
            return facts.get("FilesystemType") or facts.get("FilesystemName")
        if system == "Windows":
            kernel = ctypes.windll.kernel32
            kernel.GetVolumePathNameW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
            kernel.GetVolumePathNameW.restype = wintypes.BOOL
            kernel.GetVolumeInformationW.argtypes = [
                wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD,
                ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD),
                ctypes.POINTER(wintypes.DWORD), wintypes.LPWSTR, wintypes.DWORD,
            ]
            kernel.GetVolumeInformationW.restype = wintypes.BOOL
            volume = ctypes.create_unicode_buffer(32768)
            name = ctypes.create_unicode_buffer(256)
            if kernel.GetVolumePathNameW(str(path), volume, len(volume)) and kernel.GetVolumeInformationW(
                volume.value, None, 0, None, None, None, name, len(name)
            ):
                return name.value
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    return None


def capture_date_source(path: Path) -> dict:
    """Explain current metadata/filename candidates; not historical provenance.

    Only DateTimeOriginal is requested from exiftool, with a hard timeout. Image
    contents are never decoded or returned. A failed metadata read cannot prove
    that the filename supplied the stored date.
    """
    tool = get_exiftool_path()
    if tool is None:
        return {"source": "unknown", "reason": "ExifTool is unavailable"}
    try:
        result = subprocess.run([str(tool), "-json", "-DateTimeOriginal", str(path)],
                                capture_output=True, text=True, timeout=3, check=True)
        records = json.loads(result.stdout)
        if not isinstance(records, list) or not records or not isinstance(records[0], dict):
            return {"source": "unknown", "reason": "No metadata result"}
        if records[0].get("Error"):
            return {"source": "unknown", "reason": "Metadata could not be read"}
        raw = records[0].get("DateTimeOriginal")
        if raw:
            try:
                date = datetime.strptime(raw, "%Y:%m:%d %H:%M:%S")
                if MIN_YEAR <= date.year <= MAX_YEAR:
                    return {"source": "EXIF DateTimeOriginal", "date": date.isoformat()}
            except (ValueError, TypeError):
                pass
        candidate = get_date_from_filename(str(path))
        return {"source": "filename/path pattern" if candidate.year else "none",
                "date": candidate.date.isoformat() if candidate.date else None,
                "year": candidate.year, "month": candidate.month}
    except (OSError, ValueError, subprocess.SubprocessError):
        return {"source": "unknown", "reason": "Metadata read failed or exceeded 3 seconds"}
