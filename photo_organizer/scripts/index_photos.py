# index_photos.py
import sqlite3
import tempfile
import os
from pathlib import Path

import numpy as np
import pillow_heif
from PIL import Image
from tqdm import tqdm
import face_recognition

from organize_photos import get_photo_date
from photo_organizer.utils.image import image_from_path
from remove_duplicates import hash_image
from photo_organizer.common  import PHOTO_EXTENSIONS, DB_PATH, MEDIA_DIR, TEMP_DIR, THUMBNAIL_DIR, ROOT_DIR


# if os.path.exists(DB_PATH):
#     os.remove(DB_PATH)
#     print("DB File deleted")

def get_photo_files(root: Path):
    return [p for p in root.rglob("*") if p.suffix.lower() in PHOTO_EXTENSIONS]

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
                   FOREIGN KEY(person_id) REFERENCES people(id),
                   FOREIGN KEY(face_id) REFERENCES faces(id),
                   UNIQUE(person_id, face_id)
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

    else:
        return face_recognition.load_image_file(str(photo_path))

def save_face_thumbnail(image_path: Path, face_index: int, face_location):
    image = image_from_path(image_path)
    top, right, bottom, left = face_location
    face_image = image.crop((left, top, right, bottom))
    face_image.thumbnail((150, 150))
    stem = image_path.stem
    thumb_path = THUMBNAIL_DIR / f"face_{stem}_{face_index}.jpg"
    face_image.save(thumb_path, "JPEG")
    return thumb_path

def index_photos():
    conn = sqlite3.connect(DB_PATH)
    create_tables(conn)
    existing_files = load_existing_files(conn)

    files_to_process = [p for p in get_photo_files(MEDIA_DIR)
                        if str(p) not in existing_files]

    for photo_path in tqdm(files_to_process[0:350], desc="Indexing Photos", unit="file"):
        date_taken = get_photo_date(str(photo_path))  # Your logic here to extract from metadata

        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO photos (full_file_path, relative_file_path, hash, date_taken) VALUES (?, ?, ?, ?)",
            (
                str(photo_path),
                str(Path.relative_to(photo_path, ROOT_DIR)),
                str(hash_image(photo_path)),
                date_taken
            )
        )
        photo_id = cursor.lastrowid

        # Face recognition
        image = load_image_file(photo_path)
        face_locations = face_recognition.face_locations(image)
        face_encodings = face_recognition.face_encodings(image, face_locations)


        for i, (face_location, face_encoding) in enumerate(zip(face_locations, face_encodings)):
            thumb_path = save_face_thumbnail(photo_path, i, face_location)
            cursor.execute("INSERT INTO faces (embedding, full_file_path, relative_file_path, status, photo_id) VALUES (?, ?, ?, ?, ?)",
                           (
                               face_encoding.tobytes(),
                                str(thumb_path),
                                str(Path.relative_to(thumb_path, ROOT_DIR)),
                               "UNASSIGNED",
                               photo_id
                           ))

        conn.commit()
    conn.close()

if __name__ == "__main__":
    index_photos()
