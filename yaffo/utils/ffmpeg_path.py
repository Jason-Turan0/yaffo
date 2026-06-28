from pathlib import Path
from typing import Optional

from yaffo.common import FFMPEG_DIR
from yaffo.utils.platform_checks import IS_WINDOWS_32, IS_WINDOWS_64

def get_ffmpeg_path() -> Optional[Path]:
    """Get the app-managed ffmpeg binary under ROOT_DIR."""
    name = "ffmpeg.exe" if (IS_WINDOWS_32 or IS_WINDOWS_64) else "ffmpeg"
    ffmpeg_path = FFMPEG_DIR / name
    return ffmpeg_path if ffmpeg_path.exists() else None


def is_ffmpeg_available() -> bool:
    """Whether the app-managed ffmpeg binary is available."""
    return get_ffmpeg_path() is not None
