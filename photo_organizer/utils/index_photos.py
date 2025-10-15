import tempfile
import uuid
from pathlib import Path
from typing import List, Optional, Callable
from concurrent.futures import ProcessPoolExecutor, as_completed

import numpy as np
import pillow_heif
from PIL import Image
import face_recognition
from sqlalchemy.orm import Session

from photo_organizer.db.models import Photo, Face, FACE_STATUS_UNASSIGNED
from photo_organizer.common import PHOTO_EXTENSIONS, MEDIA_DIR, TEMP_DIR, THUMBNAIL_DIR, ROOT_DIR
from photo_organizer.utils.image import image_from_path


def get_photo_date(photo_path: str) -> Optional[str]:
    from photo_organizer.scripts.organize_photos import get_photo_date as _get_photo_date
    return _get_photo_date(photo_path)


def hash_image(photo_path: Path) -> str:
    from photo_organizer.scripts.remove_duplicates import hash_image as _hash_image
    return str(_hash_image(photo_path))


def get_photo_files(root: Path) -> List[Path]:
    return [
        p for p in root.rglob("*")
        if p.suffix.lower() in PHOTO_EXTENSIONS
        and not p.name.startswith(".")
    ]


def load_image_file(photo_path: Path) -> np.ndarray:
    if photo_path.suffix.lower() == ".heic":
        try:
            heif_file = pillow_heif.read_heif(photo_path)
            image = Image.frombytes(
                heif_file.mode,
                heif_file.size,
                heif_file.data,
                "raw",
                heif_file.mode,
                heif_file.stride,
            )

            temp_file = tempfile.NamedTemporaryFile(
                suffix=".jpg", delete=False, dir=TEMP_DIR
            )
            temp_file_path = Path(temp_file.name)
            image.save(temp_file_path, format="JPEG")
            temp_file.close()

            image_np = face_recognition.load_image_file(temp_file_path)
            temp_file_path.unlink()

            return image_np
        except Exception:
            return face_recognition.load_image_file(str(photo_path))
    else:
        return face_recognition.load_image_file(str(photo_path))


def save_face_thumbnail(image_path: Path, face_index: int, face_location) -> Path:
    image = image_from_path(image_path)
    top, right, bottom, left = face_location
    face_image = image.crop((left, top, right, bottom))
    face_image.thumbnail((150, 150))
    stem = image_path.stem
    face_id = str(uuid.uuid4())[:8]
    thumb_path = THUMBNAIL_DIR / f"face_{stem}_{face_index}_{face_id}.jpg"
    face_image.save(thumb_path, "JPEG")
    return thumb_path


def process_photo(photo_path: Path) -> Optional[dict]:
    try:
        date_taken = get_photo_date(str(photo_path))
        image = load_image_file(photo_path)

        face_locations = face_recognition.face_locations(image)
        face_embeddings = face_recognition.face_encodings(image, face_locations)

        faces_data = []
        for i, (loc, emb) in enumerate(zip(face_locations, face_embeddings)):
            thumb_path = save_face_thumbnail(photo_path, i, loc)
            top, right, bottom, left = loc
            faces_data.append({
                'embedding': emb,
                'full_file_path': str(thumb_path),
                'relative_file_path': str(thumb_path.relative_to(ROOT_DIR)),
                'location_top': top,
                'location_right': right,
                'location_bottom': bottom,
                'location_left': left
            })

        return {
            "full_file_path": str(photo_path),
            "relative_file_path": str(photo_path.relative_to(ROOT_DIR)),
            "date_taken": date_taken,
            "hash": hash_image(photo_path),
            "faces": faces_data
        }
    except Exception as e:
        print(f"Error processing {photo_path}: {e}")
        return None


def index_photos_batch(
    session: Session,
    photo_paths: List[str],
    max_workers: int = 8,
    progress_callback: Optional[Callable[[int, int], None]] = None
) -> tuple[int, int]:
    indexed_count = 0
    error_count = 0
    interrupted = False

    with ProcessPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(process_photo, Path(p)): p for p in photo_paths}

        for i, future in enumerate(as_completed(futures)):
            if interrupted:
                break

            try:
                result = future.result()
                if result:
                    photo = Photo(
                        full_file_path=result["full_file_path"],
                        relative_file_path=result["relative_file_path"],
                        hash=result["hash"],
                        date_taken=result["date_taken"]
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

                    indexed_count += 1

                    if indexed_count % 10 == 0:
                        session.commit()
                else:
                    error_count += 1

                if progress_callback:
                    progress_callback(i + 1, len(futures))
            except InterruptedError as e:
                print(f"Job interrupted, cancelling remaining tasks...")
                interrupted = True
                interrupted_error = e
                for f in futures:
                    if not f.done():
                        f.cancel()
                break
            except Exception as e:
                print(f"Error indexing photo: {e}")
                error_count += 1

        session.commit()

        if interrupted:
            raise interrupted_error

    return indexed_count, error_count


def delete_orphaned_photos(session: Session, photo_ids: List[int]) -> int:
    deleted_count = 0
    for photo_id in photo_ids:
        photo = session.query(Photo).filter(Photo.id == photo_id).first()
        if photo:
            session.delete(photo)
            deleted_count += 1

    session.commit()
    return deleted_count