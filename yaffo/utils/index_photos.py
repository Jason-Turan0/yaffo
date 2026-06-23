
import tempfile
import uuid
import platform
import subprocess
import json
from pathlib import Path
from typing import List, Optional, Callable, Tuple, Dict
from concurrent.futures import ProcessPoolExecutor, as_completed

from PIL.Image import Image as PIL_Image
from PIL import Image

import piexif
from sqlalchemy.orm import Session

from yaffo.logging_config import get_logger
from yaffo.db.models import MediaItem, Face, Tag, FACE_STATUS_UNASSIGNED
from yaffo.common import PHOTO_EXTENSIONS
from yaffo.utils.photo_dates import get_photo_date_info
from yaffo.utils.image import image_from_path, image_to_numpy
from yaffo.utils.face_analysis import detect_faces
from yaffo.utils.exiftool_path import get_exiftool_path, is_exiftool_available

logger = get_logger(__name__, 'background_tasks')

_EXIFTOOL_PATH = get_exiftool_path()
_HAS_EXIFTOOL = is_exiftool_available()

SYSTEM_FILES = {'.DS_Store', 'Thumbs.db', 'desktop.ini', '.Spotlight-V100', '.Trashes', '.fseventsd'}


def is_system_file(filename: str) -> bool:
    return filename.startswith('._') or filename in SYSTEM_FILES


def get_photo_files(root: Path) -> List[Path]:
    return [
        p for p in root.rglob("*")
        if p.suffix.lower() in PHOTO_EXTENSIONS
        and not p.name.startswith(".")
    ]


def save_face_thumbnail(
        image_path: Path,
        face_index: int,
        thumbnail_dir: Path,
        face_location) -> Path:
    image = image_from_path(image_path)
    top, right, bottom, left = face_location
    face_image = image.crop((left, top, right, bottom))
    face_image.thumbnail((150, 150))
    stem = image_path.stem
    face_id = str(uuid.uuid4())[:8]
    thumb_path = thumbnail_dir / f"face_{stem}_{face_index}_{face_id}.jpg"
    face_image.save(thumb_path, "JPEG")
    return thumb_path


def convert_to_degrees(value: Tuple) -> float:
    d = float(value[0][0]) / float(value[0][1])
    m = float(value[1][0]) / float(value[1][1])
    s = float(value[2][0]) / float(value[2][1])
    return d + (m / 60.0) + (s / 3600.0)


def get_signed_gps_from_exiftool(exif_data: Dict) -> Tuple[Optional[float], Optional[float]]:
    """Return (latitude, longitude) as signed decimal degrees.

    exiftool's ``-n`` flag reports EXIF:GPSLatitude/Longitude as unsigned
    magnitudes; the hemisphere lives in the separate ...Ref tag. The Composite
    tags are already signed, so prefer those and fall back to applying the Ref.
    """
    def signed(exif_key: str, ref_key: str, composite_key: str, negative_ref: str) -> Optional[float]:
        composite = exif_data.get(composite_key)
        if composite is not None:
            return composite
        value = exif_data.get(exif_key)
        if value is None:
            return None
        if exif_data.get(ref_key) == negative_ref:
            return -abs(value)
        return abs(value)

    latitude = signed("EXIF:GPSLatitude", "EXIF:GPSLatitudeRef", "Composite:GPSLatitude", "S")
    longitude = signed("EXIF:GPSLongitude", "EXIF:GPSLongitudeRef", "Composite:GPSLongitude", "W")
    return latitude, longitude


def get_exif_data_with_exiftool(photo_path: Path) -> Optional[Dict]:
    if not _HAS_EXIFTOOL:
        return None

    try:
        # Use -n flag to get numeric GPS coordinates instead of formatted strings
        result = subprocess.run(
            [str(_EXIFTOOL_PATH), "-json", "-G", "-n", str(photo_path)],
            capture_output=True,
            text=True,
            timeout=10
        )
        if result.returncode == 0:
            data = json.loads(result.stdout)
            return data[0] if data else None
        return None
    except (subprocess.TimeoutExpired, subprocess.CalledProcessError, json.JSONDecodeError, Exception) as e:
        logger.warning(f"Failed to get EXIF data with exiftool from {photo_path}: {e}")
        return None

def get_gps_coordinates(img: PIL_Image) -> Tuple[Optional[float], Optional[float], Optional[str]]:
    try:
        exif_data = img.info.get("exif")
        if not exif_data:
            return None, None, None

        exif_dict = piexif.load(exif_data)
        gps_info = exif_dict.get("GPS")

        if not gps_info:
            return None, None, None

        latitude = None
        longitude = None
        location_name = None

        if piexif.GPSIFD.GPSLatitude in gps_info and piexif.GPSIFD.GPSLatitudeRef in gps_info:
            lat = convert_to_degrees(gps_info[piexif.GPSIFD.GPSLatitude])
            lat_ref = gps_info[piexif.GPSIFD.GPSLatitudeRef].decode()
            if lat_ref == 'S':
                lat = -lat
            latitude = lat

        if piexif.GPSIFD.GPSLongitude in gps_info and piexif.GPSIFD.GPSLongitudeRef in gps_info:
            lon = convert_to_degrees(gps_info[piexif.GPSIFD.GPSLongitude])
            lon_ref = gps_info[piexif.GPSIFD.GPSLongitudeRef].decode()
            if lon_ref == 'W':
                lon = -lon
            longitude = lon

        return latitude, longitude, location_name
    except Exception as e:
        logger.warning(f"Failed to get GPS coordinates from image: {e}")
        return None, None, None


def _exif_field(exif_data: Dict, name: str) -> Optional[str]:
    """Look up an exiftool field by its bare name, ignoring the `-G` group prefix
    (so both "EXIF:Make" and "Make" resolve)."""
    for key, value in exif_data.items():
        if key.split(":")[-1] == name and value not in (None, ""):
            return str(value).strip()
    return None


def device_from_exif(exif_data: Dict) -> Optional[str]:
    """The capture device as one display string from EXIF Make/Model. Model often
    already starts with the make (Canon → "Canon EOS 5D Mark III"), so it isn't
    repeated; otherwise the two are joined ("FUJIFILM" + "X-T200")."""
    make = _exif_field(exif_data, "Make") or ""
    model = _exif_field(exif_data, "Model") or ""
    if not (make or model):
        return None
    if model and make and model.lower().startswith(make.lower()):
        return model
    return " ".join(part for part in (make, model) if part) or None


def parse_exiftool_to_tags(exif_data: Dict) -> List[Dict[str, str]]:
    """
    Convert exiftool JSON output to tag list format.
    Filters out datetime tags and formats as tag_name/tag_value pairs.
    """
    tags = []

    # Tags to exclude (datetime fields handled separately)
    exclude_tags = {
        "DateTimeOriginal", "DateTime", "DateTimeDigitized", "CreateDate",
        "ModifyDate", "FileModifyDate", "FileAccessDate", "FileInodeChangeDate",
        "SourceFile", "ExifToolVersion"
    }

    for key, value in exif_data.items():
        # Skip excluded tags
        if any(excluded in key for excluded in exclude_tags):
            continue

        # Convert tag name (e.g., "EXIF:Make" -> "Make")
        tag_name = key.split(":")[-1] if ":" in key else key

        # Convert value to string
        if isinstance(value, (list, tuple)):
            tag_value = ", ".join(str(v) for v in value)
        else:
            tag_value = str(value)

        if tag_value:
            tags.append({
                "tag_name": tag_name,
                "tag_value": tag_value
            })

    return tags


def extract_xmp_metadata(exif_data: Dict) -> Dict[str, any]:
    """
    Extract XMP metadata fields from exiftool output.
    Returns dict with location_name, person_names, rating, etc.
    """
    xmp_fields = {}

    # Extract location
    for key in ["XMP:Location", "IPTC:Location", "XMP-iptcCore:Location"]:
        if key in exif_data:
            xmp_fields['location_name'] = exif_data[key]
            break

    # Extract people/person tags
    person_keys = ["XMP:PersonInImage", "XMP-mwg-rs:PersonInImage", "XMP-iptcExt:PersonInImage"]
    for key in person_keys:
        if key in exif_data:
            value = exif_data[key]
            # Ensure it's a list
            if isinstance(value, str):
                xmp_fields['person_names'] = [value]
            elif isinstance(value, list):
                xmp_fields['person_names'] = value
            break

    # Extract rating
    if "XMP:Rating" in exif_data:
        try:
            xmp_fields['rating'] = int(exif_data["XMP:Rating"])
        except (ValueError, TypeError):
            pass

    # Extract keywords/subjects
    for key in ["XMP:Subject", "IPTC:Keywords", "XMP-dc:Subject"]:
        if key in exif_data:
            value = exif_data[key]
            if isinstance(value, str):
                xmp_fields['keywords'] = [value]
            elif isinstance(value, list):
                xmp_fields['keywords'] = value
            break

    return xmp_fields


def get_exif_tags(img: PIL_Image) -> List[Dict[str, str]]:
    """
    Fallback method using PIL/piexif for basic EXIF tag extraction.
    Used when exiftool is not available.
    """
    try:
        exif_data = img.info.get("exif")
        if not exif_data:
            return []

        exif_dict = piexif.load(exif_data)
        tags = []

        for ifd_name in ["0th", "Exif", "1st"]:
            ifd = exif_dict.get(ifd_name, {})
            for tag_id, value in ifd.items():
                try:
                    tag_name = piexif.TAGS[ifd_name][tag_id]["name"]

                    if tag_name in ["DateTimeOriginal", "DateTime", "DateTimeDigitized"]:
                        continue

                    if isinstance(value, bytes):
                        try:
                            value = value.decode('utf-8', errors='ignore').strip('\x00')
                        except:
                            value = str(value)
                    elif isinstance(value, tuple) and len(value) == 2:
                        numerator, denominator = value
                        if isinstance(numerator, int) and isinstance(denominator, int) and denominator != 0:
                            computed = numerator / denominator
                            value = str(int(computed)) if computed == int(computed) else str(computed)
                        else:
                            value = str(value)
                    elif isinstance(value, (list, tuple)):
                        value = str(value)
                    else:
                        value = str(value)

                    if value:
                        tags.append({
                            "tag_name": tag_name,
                            "tag_value": value
                        })
                except:
                    logger.warning(f"Failed to extract tag value from {ifd_name}: {value}")
                    continue
        return tags
    except Exception:
        logger.warning(f"Failed to extract tag value from image")
        return []

def index_photo(photo_path: Path, thumbnail_dir: Path) -> Optional[dict]:
    try:
        # Try exiftool first for comprehensive metadata (includes XMP)
        exif_data = get_exif_data_with_exiftool(photo_path)

        if exif_data:
            # Promote the capture device to a first-class photo property; the rest
            # of the EXIF payload is intentionally not persisted as tags.
            device = device_from_exif(exif_data)
            xmp_fields = extract_xmp_metadata(exif_data)

            # Extract GPS as signed decimal degrees (handles W/S hemispheres)
            latitude, longitude = get_signed_gps_from_exiftool(exif_data)

            # Get location name from XMP if available
            location_name = xmp_fields.get('location_name')
            image_tag = None
            logger.debug(f"Used exiftool for metadata extraction: {photo_path}")
        else:
            # Fallback to PIL/piexif for basic EXIF
            logger.debug(f"Falling back to PIL for metadata extraction: {photo_path}")
            image_tag = image_from_path(photo_path)
            latitude, longitude, location_name = get_gps_coordinates(image_tag)
            device = None
            xmp_fields = {}

        date_info = get_photo_date_info(str(photo_path), exif_data)

        image = image_tag if image_tag is not None else image_from_path(photo_path)
        image_numpy = image_to_numpy(image)
        detected_faces = detect_faces(image_numpy)

        faces_data = []
        for i, face in enumerate(detected_faces):
            loc = (face.location_top, face.location_right, face.location_bottom, face.location_left)
            thumb_path = save_face_thumbnail(photo_path, i, thumbnail_dir, loc)
            faces_data.append({
                'embedding': face.embedding,
                'full_file_path': str(thumb_path),
                'location_top': face.location_top,
                'location_right': face.location_right,
                'location_bottom': face.location_bottom,
                'location_left': face.location_left,
                'estimated_age': face.age,
                'gender': face.gender,
                'det_score': face.det_score,
            })

        date_taken_str = date_info.date.isoformat() if date_info.date else None

        result = {
            'full_file_path': str(photo_path),
            'date_taken': date_taken_str,
            'year': date_info.year,
            'month': date_info.month,
            'latitude': latitude,
            'longitude': longitude,
            'location_name': location_name,
            'device': device,
            'faces_data': faces_data
        }

        # Add XMP fields to result if available
        if xmp_fields:
            result['xmp_fields'] = xmp_fields

        return result

    except Exception as e:
        logger.error(f"Error processing photo {photo_path}: {e}")
        return None


def index_photos_batch(
    session: Session,
    photo_paths: List[str],
    max_workers: int = 8,
    progress_callback: Optional[Callable[[int, int], None]] = None,
    should_cancel: Optional[Callable[[], bool]] = None
) -> tuple[bool, int, int]:
    """
    Index photos in batch with optional cancellation support.

    Returns:
        tuple: (completed, indexed_count, error_count)
            - completed: True if fully completed, False if cancelled
            - indexed_count: Number of photos successfully indexed
            - error_count: Number of photos that failed to index
    """
    indexed_count = 0
    error_count = 0
    cancelled = False

    with ProcessPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(index_photo, Path(p), THUMBNAIL_DIR): p for p in photo_paths}

        for i, future in enumerate(as_completed(futures)):
            if should_cancel and should_cancel():
                print(f"Cancellation requested, stopping processing...")
                cancelled = True
                for f in futures:
                    if not f.done():
                        f.cancel()
                break

            try:
                result = future.result()
                if result:
                    media_item = MediaItem(
                        full_file_path=result["full_file_path"],
                        date_taken=result["date_taken"],
                        year=result.get("year"),
                        month=result.get("month"),
                        latitude=result.get("latitude"),
                        longitude=result.get("longitude"),
                        location_name=result.get("location_name")
                    )
                    session.add(media_item)
                    session.flush()

                    for face_data in result["faces"]:
                        face = Face(
                            embedding=face_data['embedding'].tobytes(),
                            full_file_path=face_data['full_file_path'],
                            relative_file_path=face_data['relative_file_path'],
                            status=FACE_STATUS_UNASSIGNED,
                            media_item_id=media_item.id,
                            location_top=face_data['location_top'],
                            location_right=face_data['location_right'],
                            location_bottom=face_data['location_bottom'],
                            location_left=face_data['location_left']
                        )
                        session.add(face)

                    for tag_data in result.get("tags", []):
                        tag = Tag(
                            media_item_id=media_item.id,
                            tag_name=tag_data['tag_name'],
                            tag_value=tag_data['tag_value']
                        )
                        session.add(tag)

                    indexed_count += 1

                    if indexed_count % 10 == 0:
                        session.commit()
                else:
                    error_count += 1

                if progress_callback:
                    progress_callback(i + 1, len(futures))
            except Exception as e:
                print(f"Error indexing photo: {e}")
                error_count += 1

        session.commit()

    return not cancelled, indexed_count, error_count


def unlink_face_thumbnails(thumbnail_paths: List[str]) -> None:
    """Best-effort delete of face thumbnail files. Call after the rows that
    referenced them are committed, so a rollback can't leave live faces pointing
    at missing crops."""
    for thumb in thumbnail_paths:
        try:
            Path(thumb).unlink(missing_ok=True)
        except OSError as e:
            logger.warning(f"Failed to delete face thumbnail {thumb}: {e}")


def clear_faces_for_media_items(session: Session, media_item_ids: List[int]) -> List[str]:
    """Delete every Face row for the given photos, returning their thumbnail paths
    so the caller can unlink them once the transaction commits.

    Makes re-indexing idempotent: a face's thumbnail path carries a uuid, so the
    unique constraint won't catch a re-insert -- replaying an index task would
    otherwise accumulate duplicate faces. Clearing first replaces a photo's faces
    instead. Does not commit."""
    if not media_item_ids:
        return []
    thumbnails = [
        row[0] for row in
        session.query(Face.full_file_path).filter(Face.media_item_id.in_(media_item_ids)).all()
    ]
    session.query(Face).filter(Face.media_item_id.in_(media_item_ids)).delete(synchronize_session=False)
    return thumbnails


def delete_orphaned_media_items(session: Session, media_item_ids: List[int]) -> int:
    if not media_item_ids:
        return 0

    # Capture each face's thumbnail path before the rows are gone
    face_thumbnails = [
        row[0] for row in
        session.query(Face.full_file_path).filter(Face.media_item_id.in_(media_item_ids)).all()
    ]

    # Delete dependents first (SQLite foreign keys may not cascade)
    session.query(Tag).filter(Tag.media_item_id.in_(media_item_ids)).delete(synchronize_session=False)
    deleted_faces = session.query(Face).filter(Face.media_item_id.in_(media_item_ids)).delete(synchronize_session=False)

    # Bulk delete photos using IN clause
    deleted_count = session.query(MediaItem).filter(MediaItem.id.in_(media_item_ids)).delete(synchronize_session=False)

    session.commit()

    unlink_face_thumbnails(face_thumbnails)

    logger.debug(f"Deleted {deleted_count} photos and {deleted_faces} associated faces")
    return deleted_count


def delete_media_items_by_paths(session: Session, full_file_paths: List[str]) -> int:
    """Delete photos (and their faces/tags/thumbnails) by file path.

    Resolves paths to ids and reuses delete_orphaned_media_items so the row and
    thumbnail cleanup stays in one place. Used by the file-system watcher when
    a photo is deleted or moved out of a watched directory.
    """
    if not full_file_paths:
        return 0

    media_item_ids = [
        row[0] for row in
        session.query(MediaItem.id).filter(MediaItem.full_file_path.in_(full_file_paths)).all()
    ]
    return delete_orphaned_media_items(session, media_item_ids)


def delete_media_items_under_dir(session: Session, directory: str) -> int:
    """Delete every photo whose file lives anywhere under `directory`.

    Used when a directory is renamed or moved out of a watched tree: the files
    are gone from their old paths, so their old DB rows are orphaned. The old
    paths can't be listed from disk (the directory no longer exists there), so
    the DB is the source of truth. Matching is by path ancestry, not string
    prefix, so '/m/2020' won't match '/m/2020-backup'.
    """
    directory_path = Path(directory)
    media_item_ids = [
        media_item_id
        for media_item_id, full_file_path in session.query(MediaItem.id, MediaItem.full_file_path).all()
        if directory_path in Path(full_file_path).parents
    ]
    return delete_orphaned_media_items(session, media_item_ids)


def get_orphaned_thumbnails(session: Session, thumbnail_dir: Path) -> List[Path]:
    """
    Find thumbnail files on disk that are not referenced by any Face record.

    Args:
        session: SQLAlchemy session
        thumbnail_dir: Path to the thumbnail directory

    Returns:
        List of Path objects for orphaned thumbnail files
    """
    if not thumbnail_dir or not thumbnail_dir.exists():
        return []

    # Both kinds of file living in thumbnail_dir are "referenced": face crops
    # (Face.full_file_path) and video poster frames (MediaItem.poster_path). A poster
    # is not tied to any Face, so it must be kept explicitly or it reads as orphaned.
    referenced_paths = {face.full_file_path for face in session.query(Face.full_file_path).all()}
    referenced_paths.update(
        poster for (poster,) in session.query(MediaItem.poster_path).filter(MediaItem.poster_path.isnot(None))
    )

    filesystem_thumbnails = [
        f for f in thumbnail_dir.iterdir()
        if f.is_file() and f.suffix.lower() in PHOTO_EXTENSIONS
    ]

    orphaned = [
        thumb for thumb in filesystem_thumbnails
        if str(thumb) not in referenced_paths
    ]

    return orphaned


def delete_orphaned_thumbnails(session: Session, thumbnail_dir: Path) -> Tuple[int, int]:
    """
    Delete thumbnail files that are not referenced by any Face record.

    Args:
        session: SQLAlchemy session
        thumbnail_dir: Path to the thumbnail directory

    Returns:
        Tuple of (deleted_count, error_count)
    """
    orphaned_thumbnails = get_orphaned_thumbnails(session, thumbnail_dir)

    if not orphaned_thumbnails:
        return 0, 0

    deleted_count = 0
    error_count = 0

    for thumb_path in orphaned_thumbnails:
        try:
            thumb_path.unlink()
            deleted_count += 1
            logger.debug(f"Deleted orphaned thumbnail: {thumb_path}")
        except Exception as e:
            error_count += 1
            logger.warning(f"Failed to delete orphaned thumbnail {thumb_path}: {e}")

    logger.info(f"Cleaned up {deleted_count} orphaned thumbnails ({error_count} errors)")
    return deleted_count, error_count