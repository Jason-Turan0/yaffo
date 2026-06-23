import shutil
from pathlib import Path
from typing import Optional

from yaffo.common import RESOURCES_DIR
from yaffo.logging_config import get_logger
from yaffo.utils.platform_checks import IS_WINDOWS_32, IS_WINDOWS_64

logger = get_logger(__name__)


def get_bundled_ffmpeg_path() -> Optional[Path]:
    """Path to the ffmpeg binary fetched into resources/ by packaging/download_assets.py
    (one binary for the current platform/arch). Returns None if not present."""
    name = "ffmpeg.exe" if (IS_WINDOWS_32 or IS_WINDOWS_64) else "ffmpeg"
    ffmpeg_path = RESOURCES_DIR / "ffmpeg" / name
    return ffmpeg_path if ffmpeg_path.exists() else None


def get_ffmpeg_path() -> Optional[Path]:
    """ffmpeg path, preferring the bundled binary, falling back to a system ffmpeg."""
    bundled = get_bundled_ffmpeg_path()
    if bundled:
        return bundled

    system_ffmpeg = shutil.which("ffmpeg")
    if system_ffmpeg:
        return Path(system_ffmpeg)

    return None


def is_ffmpeg_available() -> bool:
    """Whether ffmpeg is available (bundled or system)."""
    return get_ffmpeg_path() is not None
