import hashlib
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

from yaffo.db.models import MEDIA_TYPE_VIDEO
from yaffo.logging_config import get_logger
from yaffo.utils.face_analysis import DetectedFace, detect_faces
from yaffo.utils.ffmpeg_path import get_ffmpeg_path
from yaffo.utils.image import image_from_path, image_to_numpy
from yaffo.utils.index_photos import (
    _exif_field,
    device_from_exif,
    get_exif_data_with_exiftool,
    get_signed_gps_from_exiftool,
    save_face_thumbnail,
)
from yaffo.utils.photo_dates import get_date_from_filename

logger = get_logger(__name__)

# Offset used when a clip has no probe-able duration: grab an early frame rather
# than risk seeking past the end.
_POSTER_FALLBACK_OFFSET = 1.0
_FRAME_TIMEOUT_SECONDS = 60

# Face sampling (docs/video.md Phase 2). A clip is mostly near-duplicate frames, so
# sample sparsely and bound the cost: one frame every _FACE_SAMPLE_INTERVAL seconds,
# capped at _FACE_SAMPLE_MAX_FRAMES. Each sampled frame costs ~one photo's detection.
_FACE_SAMPLE_INTERVAL = 3.0
_FACE_SAMPLE_MAX_FRAMES = 20
# Within-clip same-person dedup. Same person, same scene → very high cosine; this
# sits well below the same-person median (~0.66) yet above cross-person scores, so it
# collapses duplicates without merging two different people. (compare_utils band.)
_FACE_DEDUP_SIMILARITY = 0.5
# Reject faces whose smaller bbox side is below this (px). Below ~ArcFace's 112px
# input the crop is mostly interpolation, so the embedding is unreliable — common in
# video, where anyone not in close-up is tiny in the frame. Dropping these keeps junk
# detections out of people clustering rather than relying on dedup to bury them.
_FACE_MIN_SIZE_PX = 50


def _poster_path_for(video_path: Path, thumbnail_dir: Path) -> Path:
    """Deterministic poster filename (stable per source path) so a re-index
    overwrites in place instead of leaking a new file each time."""
    digest = hashlib.sha1(str(video_path).encode("utf-8")).hexdigest()[:16]
    return thumbnail_dir / f"poster_{digest}.jpg"


def _grab_frame(ffmpeg: Path, video_path: Path, offset_seconds: float, out_path: Path) -> bool:
    """Extract a single frame at offset_seconds to out_path (JPEG) via a fast seek.
    ffmpeg auto-applies the container's rotation so the frame is oriented correctly.
    Returns whether the frame was written."""
    try:
        result = subprocess.run(
            [
                str(ffmpeg), "-hide_banner", "-loglevel", "error",
                "-ss", f"{offset_seconds:.3f}", "-i", str(video_path),
                "-frames:v", "1", "-q:v", "3", "-y", str(out_path),
            ],
            capture_output=True,
            timeout=_FRAME_TIMEOUT_SECONDS,
        )
    except (subprocess.TimeoutExpired, OSError) as e:
        logger.warning("ffmpeg frame grab failed for %s @ %.2fs: %s", video_path, offset_seconds, e)
        return False
    if result.returncode == 0 and out_path.exists():
        return True
    logger.warning("ffmpeg frame grab failed for %s @ %.2fs: %s",
                   video_path, offset_seconds, result.stderr.decode(errors="replace"))
    return False


def extract_poster(video_path: Path, thumbnail_dir: Path, duration_seconds: Optional[float]) -> Optional[Path]:
    """Grab a representative still (the middle frame) via ffmpeg and write it to
    thumbnail_dir. Returns the poster path, or None if ffmpeg is unavailable or
    fails — callers fall back to the static placeholder."""
    ffmpeg = get_ffmpeg_path()
    if ffmpeg is None:
        logger.warning("ffmpeg not available; skipping poster for %s", video_path)
        return None

    offset = duration_seconds / 2 if duration_seconds and duration_seconds > 0 else _POSTER_FALLBACK_OFFSET
    poster_path = _poster_path_for(video_path, thumbnail_dir)
    thumbnail_dir.mkdir(parents=True, exist_ok=True)
    return poster_path if _grab_frame(ffmpeg, video_path, offset, poster_path) else None


def _sample_offsets(duration_seconds: Optional[float]) -> list[float]:
    """Evenly-spaced sample timestamps across the clip (excluding the very start/end),
    one per _FACE_SAMPLE_INTERVAL seconds, capped at _FACE_SAMPLE_MAX_FRAMES. Falls
    back to a single early frame when the duration is unknown."""
    if not duration_seconds or duration_seconds <= 0:
        return [_POSTER_FALLBACK_OFFSET]
    n = min(_FACE_SAMPLE_MAX_FRAMES, max(1, int(duration_seconds // _FACE_SAMPLE_INTERVAL)))
    return [duration_seconds * (i + 1) / (n + 1) for i in range(n)]


@dataclass
class _SampledFace:
    """A face detected in one sampled frame, with the precomputed quality score used to
    pick the representative crop when the same person appears across several frames."""
    face: DetectedFace
    frame: Path
    quality: float


def _face_min_side(face: DetectedFace) -> int:
    """The shorter side of the (clamped) face box, in px."""
    return min(face.location_right - face.location_left, face.location_bottom - face.location_top)


def _face_quality(image_rgb: np.ndarray, face: DetectedFace) -> float:
    """Heuristic crop-quality score for choosing the best frame of a person across a
    clip. Higher is better. Combines (multiplicatively, so any near-zero factor tanks
    the crop): detector confidence, face size, and sharpness (variance of the
    Laplacian on the grayscale crop — low for the motion/compression blur that's rife
    in video). Used only for ranking within a person, so absolute scale is irrelevant."""
    crop = image_rgb[face.location_top:face.location_bottom, face.location_left:face.location_right]
    if crop.size == 0:
        return 0.0
    gray = cv2.cvtColor(crop, cv2.COLOR_RGB2GRAY)
    sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    return (face.det_score or 0.0) * _face_min_side(face) * sharpness


def _dedup_faces(detected: list[_SampledFace]) -> list[_SampledFace]:
    """Collapse the same person seen across multiple frames into one face, keeping the
    highest-quality crop (size + sharpness + confidence, not confidence alone — a
    blurry close-up can out-score a sharp one on det_score). Greedy single-link
    clustering on the L2-normalized ArcFace embeddings (dot product == cosine); a face
    joins the first cluster it's within _FACE_DEDUP_SIMILARITY of."""
    reps: list[_SampledFace] = []
    for sampled in detected:
        best_idx, best_sim = -1, -1.0
        for idx, rep in enumerate(reps):
            sim = float(np.dot(sampled.face.embedding, rep.face.embedding))
            if sim > best_sim:
                best_sim, best_idx = sim, idx
        if best_idx >= 0 and best_sim >= _FACE_DEDUP_SIMILARITY:
            if sampled.quality > reps[best_idx].quality:
                reps[best_idx] = sampled
        else:
            reps.append(sampled)
    return reps


def extract_sample_frames(video_path: Path, out_dir: Path, duration_seconds: Optional[float]) -> list[Path]:
    """Extract the evenly-spaced sample frames (see _sample_offsets) into out_dir and
    return the written paths. Shared by the face detector and the CLIP labeler so they
    sample identically. Empty list if ffmpeg is unavailable. The caller owns out_dir's
    lifecycle (typically a TemporaryDirectory)."""
    ffmpeg = get_ffmpeg_path()
    if ffmpeg is None:
        return []
    out_dir.mkdir(parents=True, exist_ok=True)
    frames = []
    for idx, offset in enumerate(_sample_offsets(duration_seconds)):
        # Lossless PNG (not JPEG): faces are detected and embedded straight from these,
        # so re-compressing an already-compressed video frame would only add artifacts
        # for ArcFace to see. The poster (display-only) stays JPEG.
        frame_path = out_dir / f"{video_path.stem}_f{idx}.png"
        if _grab_frame(ffmpeg, video_path, offset, frame_path):
            frames.append(frame_path)
    return frames


def iter_video_frame_arrays(video_path: Path, duration_seconds: Optional[float]):
    """Yield each sampled frame as an RGB numpy array. The frame files live in a temp
    dir cleaned up when iteration finishes, so consume it eagerly (don't hold the
    arrays past what you need). Yields nothing if ffmpeg is unavailable."""
    if get_ffmpeg_path() is None:
        return
    with tempfile.TemporaryDirectory(prefix="yaffo_frames_") as tmp:
        for frame_path in extract_sample_frames(video_path, Path(tmp), duration_seconds):
            yield image_to_numpy(image_from_path(frame_path))


def detect_video_faces(video_path: Path, thumbnail_dir: Path, duration_seconds: Optional[float]) -> list[dict]:
    """Sample frames across the clip, run InsightFace on each, dedup the same person
    across frames, and return faces_data in the same shape index_photo produces (so
    the DB-write half of index_photo_task is shared). Empty list if ffmpeg is
    unavailable or no faces are found."""
    if get_ffmpeg_path() is None:
        return []

    thumbnail_dir.mkdir(parents=True, exist_ok=True)
    detected: list[_SampledFace] = []
    with tempfile.TemporaryDirectory(prefix="yaffo_frames_") as tmp:
        for frame_path in extract_sample_frames(video_path, Path(tmp), duration_seconds):
            image_numpy = image_to_numpy(image_from_path(frame_path))
            for face in detect_faces(image_numpy):
                if _face_min_side(face) < _FACE_MIN_SIZE_PX:
                    continue
                quality = _face_quality(image_numpy, face)
                detected.append(_SampledFace(face=face, frame=frame_path, quality=quality))

        # Thumbnails must be cropped from the frame files before the temp dir is
        # cleaned up (save_face_thumbnail re-reads the source); they're written to the
        # persistent thumbnail_dir.
        faces_data = []
        for i, sampled in enumerate(_dedup_faces(detected)):
            face, frame_path = sampled.face, sampled.frame
            loc = (face.location_top, face.location_right, face.location_bottom, face.location_left)
            thumb_path = save_face_thumbnail(frame_path, i, thumbnail_dir, loc)
            faces_data.append({
                "embedding": face.embedding,
                "full_file_path": str(thumb_path),
                "location_top": face.location_top,
                "location_right": face.location_right,
                "location_bottom": face.location_bottom,
                "location_left": face.location_left,
                "estimated_age": face.age,
                "gender": face.gender,
                "det_score": face.det_score,
            })
    return faces_data

# exiftool date tags to try, in order of preference. QuickTime CreateDate is UTC by
# spec, but for v1 we keep it naive wall-clock like photo date_taken (docs/video.md);
# DateTimeOriginal (when a camera writes it) is already local. The later tags cover
# the non-MP4 containers (Matroska DateUTC, ASF/WMV CreationDate/CreationTime).
_DATE_TAGS = (
    "DateTimeOriginal", "CreationDate", "CreateDate", "MediaCreateDate",
    "TrackCreateDate", "DateUTC", "CreationTime",
)
# exiftool's codec field is container-specific: QuickTime/MP4 -> CompressorID,
# Matroska -> VideoCodecID, AVI -> VideoCodec/Compression, ASF/WMV -> VideoCodecName,
# FLV -> VideoEncoding. Try them in order and keep the first present.
_CODEC_TAGS = (
    "CompressorID", "VideoCodecID", "VideoCodec", "VideoCodecName",
    "VideoEncoding", "Compression",
)
# Known codec ids normalized to a human name; anything else is stored as-is.
_CODEC_NAMES = {"avc1": "h264", "hvc1": "hevc", "hev1": "hevc", "mp4v": "mpeg4"}


def _codec(exif_data: dict) -> Optional[str]:
    for tag in _CODEC_TAGS:
        raw = _exif_field(exif_data, tag)
        if raw:
            return _CODEC_NAMES.get(raw.lower(), raw)
    return None


def _display_dimensions(exif_data: dict) -> tuple[Optional[int], Optional[int]]:
    """The video's *displayed* width/height. exiftool reports the raw stored frame
    size; a phone clip is usually stored landscape with a Rotation flag, so for a
    90°/270° rotation we swap the axes to match how it actually plays (and how the
    ffmpeg poster, which honours rotation, looks)."""
    width = _to_int(_exif_field(exif_data, "ImageWidth"))
    height = _to_int(_exif_field(exif_data, "ImageHeight"))
    rotation = _to_int(_exif_field(exif_data, "Rotation"))
    if rotation in (90, 270) and width is not None and height is not None:
        return height, width
    return width, height


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
    index_photo_task is shared: exiftool metadata, an ffmpeg-extracted poster frame,
    and faces detected on sampled frames (deduped per clip). Returns None on a hard
    failure; poster/faces degrade to None/[] when ffmpeg is unavailable."""
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

        codec = _codec(exif_data)
        width, height = _display_dimensions(exif_data)
        duration = _to_float(_exif_field(exif_data, "Duration"))

        poster = extract_poster(video_path, thumbnail_dir, duration)
        faces_data = detect_video_faces(video_path, thumbnail_dir, duration)

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
            "faces_data": faces_data,
            "duration_seconds": duration,
            "width": width,
            "height": height,
            "video_codec": codec,
            "poster_path": str(poster) if poster else None,
        }
    except Exception as e:
        logger.error(f"Error processing video {video_path}: {e}")
        return None
