from pathlib import Path
from typing import Optional

from yaffo.common import EXIFTOOL_DIR
from yaffo.utils.platform_checks import IS_WINDOWS_32, IS_WINDOWS_64


EXIFTOOL_VERSION = "13.40"


def get_exiftool_resource_path() -> Path:
    return EXIFTOOL_DIR


def get_exiftool_path() -> Optional[Path]:
    """Get the app-managed ExifTool path under ROOT_DIR."""
    resource_path = get_exiftool_resource_path()
    if IS_WINDOWS_64:
        exiftool_path = resource_path / "bin" / f"exiftool-{EXIFTOOL_VERSION}_64" / "exiftool.exe"
    elif IS_WINDOWS_32:
        exiftool_path = resource_path / "bin" / f"exiftool-{EXIFTOOL_VERSION}_32" / "exiftool.exe"
    else:
        exiftool_path = resource_path / "src" / "exiftool"

    if exiftool_path.exists():
        return exiftool_path
    return None


def is_exiftool_available() -> bool:
    """Check if the app-managed ExifTool package is available."""
    return get_exiftool_path() is not None
