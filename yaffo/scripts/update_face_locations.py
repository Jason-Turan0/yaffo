"""
Update face locations for existing faces in the database.

This script extracts the face_index from the face thumbnail filename,
re-processes the original photo to get face locations, and updates
the database with the correct location coordinates.
"""

import re
import sqlite3
from pathlib import Path
from tqdm import tqdm
from concurrent.futures import ProcessPoolExecutor, as_completed

from yaffo.common import DB_PATH, ROOT_DIR
from yaffo.scripts.index_photos import load_image_file
import face_recognition

# Configuration
max_workers = 8


def extract_face_index_from_path(image_path: Path, relative_file_path: Path) -> int | None:
    path = Path(relative_file_path)
    filename = path.stem
    stem = image_path.stem
    # Pattern: face_{original_stem}_{index}_{uuid}
    match = re.match(f'face_{stem}_(\d+)', filename)
    if match:
        face_index = int(match.group(1))
        return face_index

    return None


def process_photo_faces(photo_path: str, faces: list[dict]) -> list[tuple] | None:
    """
    Process a single photo and return face location updates.

    Args:
        photo_path: Relative path to the photo
        faces: List of face data dicts with 'id', 'index', 'path'

    Returns:
        List of tuples (face_id, top, right, bottom, left) or None on error
    """
    try:
        # Load image and detect faces
        full_photo_path = ROOT_DIR / photo_path

        if not full_photo_path.exists():
            print(f"Warning: Photo not found: {full_photo_path}")
            return None

        image = load_image_file(full_photo_path)
        face_locations = face_recognition.face_locations(image)

        updates = []

        # Update each face for this photo
        for face_data in faces:
            face_index = face_data['index']

            # Check if face_index is valid
            if face_index >= len(face_locations):
                print(f"Warning: Face index {face_index} out of range for {photo_path} (has {len(face_locations)} faces)")
                continue

            # Get location for this face
            top, right, bottom, left = face_locations[face_index]

            # Add to updates list
            updates.append((face_data['id'], top, right, bottom, left))

        return updates

    except Exception as e:
        print(f"Error processing {photo_path}: {e}")
        return None


def update_face_locations():
    """Update location coordinates for all faces in the database."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Get all faces that don't have location data yet
    cursor.execute("""
        SELECT f.id, f.relative_file_path, p.relative_file_path as photo_path
        FROM faces f
        JOIN photos p ON f.photo_id = p.id
        WHERE f.location_top IS NULL
    """)

    faces_to_update = cursor.fetchall()

    if not faces_to_update:
        print("No faces need location updates!")
        return

    print(f"Found {len(faces_to_update)} faces to update")

    # Group faces by photo to minimize image loading
    faces_by_photo = {}
    for face_id, face_path, photo_path in faces_to_update:
        face_index = extract_face_index_from_path(Path(photo_path), Path(face_path))

        if face_index is None:
            print(f"Warning: Could not extract face index from {face_path}")
            continue

        if photo_path not in faces_by_photo:
            faces_by_photo[photo_path] = []

        faces_by_photo[photo_path].append({
            'id': face_id,
            'index': face_index,
            'path': face_path
        })

    print(f"Processing {len(faces_by_photo)} unique photos with {max_workers} workers")

    updates_made = 0
    errors = 0

    # Process photos in parallel
    with ProcessPoolExecutor(max_workers=max_workers) as executor:
        # Submit all jobs
        futures = []
        photo_paths = []
        for photo_path, faces in faces_by_photo.items():
            future = executor.submit(process_photo_faces, photo_path, faces)
            futures.append(future)
            photo_paths.append(photo_path)

        # Collect results as they complete
        for future in tqdm(as_completed(futures), total=len(futures), desc="Processing photos", unit="photo"):
            result = future.result()
            if result is None:
                # Error occurred (already logged in worker)
                errors += 1
                continue

            for update in result:
                face_id, top, right, bottom, left = update
                cursor.execute("""
                           UPDATE faces
                           SET location_top = ?,
                               location_right = ?,
                               location_bottom = ?,
                               location_left = ?
                           WHERE id = ?
                       """, (top, right, bottom, left, face_id))
                conn.commit()

    conn.close()

    print(f"\nUpdate complete!")
    print(f"Successfully updated: {updates_made} faces")
    print(f"Photos with errors: {errors}")


if __name__ == "__main__":
    update_face_locations()