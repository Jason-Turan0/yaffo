import shutil
from pathlib import Path
from typing import Optional
import platform

from yaffo.logging_config import get_logger
from yaffo.utils.platform_checks import IS_MAC, IS_LINUX, IS_WINDOWS_32, IS_WINDOWS_64, _SYSTEM

logger = get_logger(__name__)


def get_exiftool_resource_path() -> Path:
    return Path(__file__).parent.parent.parent / 'resources' / 'Image-ExifTool-13.40'


def get_bundled_exiftool_path() -> Optional[Path]:
    """
    Get path to bundled exiftool binary.
    Returns None if not found.
    """
    resource_path = get_exiftool_resource_path()
    if IS_MAC or IS_LINUX:
        exiftool_path = resource_path / 'src' / 'exiftool'
    elif IS_WINDOWS_32:
        exiftool_path = resource_path / 'bin'  / 'exiftool-13.40_32' / 'exiftool.exe'
    elif IS_WINDOWS_64:
        exiftool_path = resource_path / 'bin' / 'exiftool-13.40_64' / 'exiftool.exe'
    else:
        logger.warning(f"unsupported platform for exiftool {_SYSTEM}")
        exiftool_path = None

    if exiftool_path.exists():
        return exiftool_path
    return None


def get_exiftool_path() -> Optional[Path]:
    """
    Get exiftool path, preferring bundled version.
    Falls back to system exiftool if available.
    """
    bundled = get_bundled_exiftool_path()
    if bundled:
        return bundled

    system_exiftool = shutil.which('exiftool')
    if system_exiftool:
        return Path(system_exiftool)

    return None


def is_exiftool_available() -> bool:
    """Check if exiftool is available (bundled or system)."""
    return get_exiftool_path() is not None
