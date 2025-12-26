
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

import face_recognition
import piexif
from sqlalchemy.orm import Session

from yaffo.logging_config import get_logger
from yaffo.db.models import Photo, Face, Tag, FACE_STATUS_UNASSIGNED
from yaffo.common import PHOTO_EXTENSIONS, TEMP_DIR, THUMBNAIL_DIR, ROOT_DIR
from yaffo.utils.photo_dates import PhotoDateInfo, get_photo_date_info
from yaffo.utils.image import image_from_path, image_to_numpy
from yaffo.utils.exiftool_path import get_exiftool_path, is_exiftool_available

logger = get_logger(__name__, 'background_tasks')

_EXIFTOOL_PATH = get_exiftool_path()
_HAS_EXIFTOOL = is_exiftool_available()

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
            # Parse exiftool output
            tags = parse_exiftool_to_tags(exif_data)
            xmp_fields = extract_xmp_metadata(exif_data)

            # Extract GPS from exiftool data (exiftool returns decimal degrees)
            latitude = exif_data.get("EXIF:GPSLatitude") or exif_data.get("Composite:GPSLatitude")
            longitude = exif_data.get("EXIF:GPSLongitude") or exif_data.get("Composite:GPSLongitude")

            # Get location name from XMP if available
            location_name = xmp_fields.get('location_name')
            image_tag = None
            logger.debug(f"Used exiftool for metadata extraction: {photo_path}")
        else:
            # Fallback to PIL/piexif for basic EXIF
            logger.debug(f"Falling back to PIL for metadata extraction: {photo_path}")
            image_tag = image_from_path(photo_path)
            latitude, longitude, location_name = get_gps_coordinates(image_tag)
            tags = get_exif_tags(image_tag)
            xmp_fields = {}

        date_info = get_photo_date_info(str(photo_path), exif_data)

        image = image_tag if image_tag is not None else image_from_path(photo_path)
        image_numpy = image_to_numpy(image)
        face_locations = face_recognition.face_locations(image_numpy)
        face_embeddings = face_recognition.face_encodings(image_numpy, face_locations)

        faces_data = []
        for i, (loc, emb) in enumerate(zip(face_locations, face_embeddings)):
            thumb_path = save_face_thumbnail(photo_path, i, thumbnail_dir, loc)
            top, right, bottom, left = loc
            faces_data.append({
                'embedding': emb,
                'full_file_path': str(thumb_path),
                'location_top': top,
                'location_right': right,
                'location_bottom': bottom,
                'location_left': left
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
            'tags': tags,
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
                    photo = Photo(
                        full_file_path=result["full_file_path"],
                        date_taken=result["date_taken"],
                        year=result.get("year"),
                        month=result.get("month"),
                        latitude=result.get("latitude"),
                        longitude=result.get("longitude"),
                        location_name=result.get("location_name")
                    )
                    session.add(photo)
                    session.flush()

                    for face_data in result["faces"]:
                        face = Face(
                            embedding=face_data['embedding'].tobytes(),
                            full_file_path=face_data['full_file_path'],
                            relative_file_path=face_data['relative_file_path'],
                            status=FACE_STATUS_UNASSIGNED,
                            photo_id=photo.id,
                            location_top=face_data['location_top'],
                            location_right=face_data['location_right'],
                            location_bottom=face_data['location_bottom'],
                            location_left=face_data['location_left']
                        )
                        session.add(face)

                    for tag_data in result.get("tags", []):
                        tag = Tag(
                            photo_id=photo.id,
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


def delete_orphaned_photos(session: Session, photo_ids: List[int]) -> int:
    if not photo_ids:
        return 0

    # Delete related faces first (SQLite foreign keys may not cascade)
    deleted_faces = session.query(Face).filter(Face.photo_id.in_(photo_ids)).delete(synchronize_session=False)

    # Bulk delete photos using IN clause
    deleted_count = session.query(Photo).filter(Photo.id.in_(photo_ids)).delete(synchronize_session=False)

    session.commit()
    logger.debug(f"Deleted {deleted_count} photos and {deleted_faces} associated faces")
    return deleted_count


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

    db_thumbnail_paths = {face.full_file_path for face in session.query(Face.full_file_path).all()}

    filesystem_thumbnails = [
        f for f in thumbnail_dir.iterdir()
        if f.is_file() and f.suffix.lower() in PHOTO_EXTENSIONS
    ]

    orphaned = [
        thumb for thumb in filesystem_thumbnails
        if str(thumb) not in db_thumbnail_paths
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