from pathlib import Path
from typing import Optional

from yaffo.common import ROOT_DIR
from yaffo.utils.platform_checks import IS_WINDOWS_32, IS_WINDOWS_64


EXIFTOOL_DIR_PREFIX = "Image-ExifTool-"


def _version_key(path: Path) -> tuple[int, ...]:
    version = path.name.removeprefix(EXIFTOOL_DIR_PREFIX)
    return tuple(int(part) for part in version.split(".") if part.isdigit())


def _exiftool_dirs() -> list[Path]:
    return sorted(
        (path for path in ROOT_DIR.glob(f"{EXIFTOOL_DIR_PREFIX}*") if path.is_dir()),
        key=_version_key,
        reverse=True,
    )


def get_exiftool_resource_path() -> Path:
    dirs = _exiftool_dirs()
    if dirs:
        return dirs[0]
    return ROOT_DIR / "Image-ExifTool"


def _candidate_paths(resource_path: Path) -> list[Path]:
    version = resource_path.name.removeprefix(EXIFTOOL_DIR_PREFIX)
    if IS_WINDOWS_64:
        return [
            resource_path / "bin" / f"exiftool-{version}_64" / "exiftool.exe",
            resource_path / "exiftool.exe",
        ]
    if IS_WINDOWS_32:
        return [
            resource_path / "bin" / f"exiftool-{version}_32" / "exiftool.exe",
            resource_path / "exiftool.exe",
        ]
    return [resource_path / "src" / "exiftool"]


def get_exiftool_path() -> Optional[Path]:
    """Get the app-managed ExifTool path under ROOT_DIR."""
    for resource_path in _exiftool_dirs():
        for exiftool_path in _candidate_paths(resource_path):
            if exiftool_path.exists():
                return exiftool_path
    return None


def is_exiftool_available() -> bool:
    """Check if the app-managed ExifTool package is available."""
    return get_exiftool_path() is not None
