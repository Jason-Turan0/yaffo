# index_photos.py
import sqlite3
import tempfile
import uuid
from pathlib import Path

import numpy as np
import pillow_heif
from PIL import Image
from tqdm import tqdm
import face_recognition
from concurrent.futures import ProcessPoolExecutor, as_completed


from organize_photos import get_photo_date
from photo_organizer.db.models import FACE_STATUS_UNASSIGNED
from photo_organizer.utils.image import image_from_path
from remove_duplicates import hash_image
from photo_organizer.common  import PHOTO_EXTENSIONS, DB_PATH, MEDIA_DIR, TEMP_DIR, THUMBNAIL_DIR, ROOT_DIR


# if os.path.exists(DB_PATH):
#     os.remove(DB_PATH)
#     print("DB File deleted")

def get_photo_files(root: Path):
    return [p for p
            in root.rglob("*")
            if p.suffix.lower() in PHOTO_EXTENSIONS
            and not p.name.startswith(".")
            ]

def create_tables(conn):
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS photos (
            id INTEGER PRIMARY KEY,
            full_file_path TEXT UNIQUE,
            relative_file_path TEXT UNIQUE,            
            hash TEXT,
            date_taken TEXT
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS faces (
            id INTEGER PRIMARY KEY,
            embedding BLOB,
            full_file_path TEXT UNIQUE,
            relative_file_path TEXT UNIQUE,
            photo_id INTEGER,
            status TEXT,
            location_top INTEGER,
            location_right INTEGER,
            location_bottom INTEGER,
            location_left INTEGER,
            FOREIGN KEY(photo_id) REFERENCES photos(id)
        )
    """)
    cursor.execute("""
           CREATE TABLE IF NOT EXISTS people (
               id INTEGER PRIMARY KEY,
               name TEXT,
               avg_embedding BLOB
           )
       """)
    cursor.execute("""
               CREATE TABLE IF NOT EXISTS people_face (
                   person_id INTEGER,
                   face_id INTEGER,
                   similarity REAL,
                   FOREIGN KEY(person_id) REFERENCES people(id),
                   FOREIGN KEY(face_id) REFERENCES faces(id),
                   UNIQUE(person_id, face_id)
               )
           """)
    cursor.execute("""
                CREATE TABLE IF NOT EXISTS people_embeddings (
                    person_id INTEGER NOT NULL,
                    year INTEGER NOT NULL,
                    avg_embedding BLOB NOT NULL,
                    included_face_ids TEXT,
                    PRIMARY KEY (person_id, year),
                    FOREIGN KEY (person_id) REFERENCES people(id) ON DELETE CASCADE
                )
    """)
    conn.commit()

def load_existing_files(conn):
    cursor = conn.cursor()
    cursor.execute("SELECT full_file_path FROM photos")
    return set(row[0] for row in cursor.fetchall())

def load_image_file(photo_path: Path) -> np.ndarray:
    """
    Load image for face recognition.
    HEIC files are converted to temporary JPEGs automatically.
    """
    if photo_path.suffix.lower() == ".heic":
        # Convert HEIC to temporary JPEG
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
            temp_file.close()  # close so face_recognition can open it

            # Load the converted file into numpy array
            image_np = face_recognition.load_image_file(temp_file_path)

            # Delete the temp file after loading
            temp_file_path.unlink()

            return image_np
        except Exception:
            return face_recognition.load_image_file(str(photo_path))

    else:
        return face_recognition.load_image_file(str(photo_path))

def save_face_thumbnail(image_path: Path, face_index: int, face_location):
    image = image_from_path(image_path)
    top, right, bottom, left = face_location
    face_image = image.crop((left, top, right, bottom))
    face_image.thumbnail((150, 150))
    stem = image_path.stem
    face_id = str(uuid.uuid4())[:8]
    thumb_path = THUMBNAIL_DIR / f"face_{stem}_{face_index}_{face_id}.jpg"
    face_image.save(thumb_path, "JPEG")
    return thumb_path

MAX_WIDTH = 800

def process_photo(photo_path):
    """Load image, detect faces, create embeddings, generate thumbnails."""
    try:
        date_taken = get_photo_date(str(photo_path))
        image = load_image_file(photo_path)

        face_locations = face_recognition.face_locations(image)
        face_embeddings = face_recognition.face_encodings(image, face_locations)

        faces_data = []
        for i, (loc, emb) in enumerate(zip(face_locations, face_embeddings)):
            thumb_path = save_face_thumbnail(photo_path, i, loc)
            top, right, bottom, left = loc
            faces_data.append((emb, str(thumb_path), str(Path.relative_to(thumb_path, ROOT_DIR)), top, right, bottom, left))

        return {
            "photo_path": str(photo_path),
            "rel_path": str(Path.relative_to(photo_path, ROOT_DIR)),
            "date_taken": date_taken,
            "hash": str(hash_image(photo_path)),
            "faces": faces_data
        }
    except Exception as e:
        print(f"Error processing {photo_path}: {e}")
        return None
max_workers = 8
batch_size = 50

def index_photos():
    conn = sqlite3.connect(DB_PATH)
    create_tables(conn)
    existing_files = load_existing_files(conn)

    files_to_process = [p for p in get_photo_files(MEDIA_DIR)
                        if str(p) not in existing_files]
    futures = []
    cursor = conn.cursor()
    with ProcessPoolExecutor(max_workers=max_workers) as executor:
        for photo_path in files_to_process:
            futures.append(executor.submit(process_photo, photo_path))

        for i, future in enumerate(tqdm(as_completed(futures), total=len(futures), desc="Indexing Photos", unit="file")):
            result = future.result()
            if not result:
                continue

            cursor.execute(
                "INSERT INTO photos (full_file_path, relative_file_path, hash, date_taken) VALUES (?, ?, ?, ?)",
                (
                    result["photo_path"],
                    result["rel_path"],
                    result["hash"],
                    result["date_taken"]
                )
            )
            photo_id = cursor.lastrowid
            for emb, full_thumb, rel_thumb, top, right, bottom, left in result["faces"]:
                cursor.execute(
                    "INSERT INTO faces (embedding, full_file_path, relative_file_path, status, photo_id, location_top, location_right, location_bottom, location_left) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (emb.tobytes(), full_thumb, rel_thumb, FACE_STATUS_UNASSIGNED, photo_id, top, right, bottom, left)
                )
            if i > 0 and i % batch_size == 0:
                conn.commit()

    conn.close()

if __name__ == "__main__":
    index_photos()
