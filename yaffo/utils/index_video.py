from datetime import datetime
from pathlib import Path
from typing import Optional

from yaffo.db.models import MEDIA_TYPE_VIDEO
from yaffo.logging_config import get_logger
from yaffo.utils.index_photos import (
    _exif_field,
    device_from_exif,
    get_exif_data_with_exiftool,
    get_signed_gps_from_exiftool,
)
from yaffo.utils.photo_dates import get_date_from_filename

logger = get_logger(__name__)

# exiftool date tags to try, in order of preference. QuickTime CreateDate is UTC by
# spec, but for v1 we keep it naive wall-clock like photo date_taken (docs/video.md);
# DateTimeOriginal (when a camera writes it) is already local.
_DATE_TAGS = ("DateTimeOriginal", "CreationDate", "CreateDate", "MediaCreateDate", "TrackCreateDate")
# Container CompressorID (avc1/hvc1/...) normalized to the human codec name.
_CODEC_NAMES = {"avc1": "h264", "hvc1": "hevc", "hev1": "hevc", "mp4v": "mpeg4"}


def _parse_video_date(exif_data: dict) -> Optional[datetime]:
    for tag in _DATE_TAGS:
        raw = _exif_field(exif_data, tag)
        if not raw:
            continue
        # exiftool emits "YYYY:MM:DD HH:MM:SS" (19 chars) optionally followed by a
        # tz offset; the fixed-width prefix drops the offset and any sub-seconds.
        stamp = raw.strip()[:19]
        try:
            return datetime.strptime(stamp, "%Y:%m:%d %H:%M:%S")
        except ValueError:
            continue
    return None


def _to_int(value: Optional[str]) -> Optional[int]:
    try:
        return int(float(value)) if value is not None else None
    except (TypeError, ValueError):
        return None


def _to_float(value: Optional[str]) -> Optional[float]:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def index_video(video_path: Path, thumbnail_dir: Path) -> Optional[dict]:
    """Index a video the same shape index_photo returns, so the DB-write half of
    index_photo_task is shared. Phase 1: exiftool metadata only — no poster frame
    (a static placeholder stands in, docs/video.md) and no faces. `thumbnail_dir`
    is accepted for call-site parity and reserved for the Phase 3 poster."""
    try:
        exif_data = get_exif_data_with_exiftool(video_path) or {}

        latitude, longitude = get_signed_gps_from_exiftool(exif_data)
        date = _parse_video_date(exif_data)
        if date is None:
            filename_info = get_date_from_filename(str(video_path))
            year, month = filename_info.year, filename_info.month
            date_taken = filename_info.date.isoformat() if filename_info.date else None
        else:
            year, month, date_taken = date.year, date.month, date.isoformat()

        codec_raw = _exif_field(exif_data, "CompressorID")
        codec = _CODEC_NAMES.get((codec_raw or "").lower(), codec_raw)

        return {
            "media_type": MEDIA_TYPE_VIDEO,
            "full_file_path": str(video_path),
            "date_taken": date_taken,
            "year": year,
            "month": month,
            "latitude": latitude,
            "longitude": longitude,
            "location_name": None,
            "device": device_from_exif(exif_data),
            "faces_data": [],
            "duration_seconds": _to_float(_exif_field(exif_data, "Duration")),
            "width": _to_int(_exif_field(exif_data, "ImageWidth")),
            "height": _to_int(_exif_field(exif_data, "ImageHeight")),
            "video_codec": codec,
        }
    except Exception as e:
        logger.error(f"Error processing video {video_path}: {e}")
        return None
