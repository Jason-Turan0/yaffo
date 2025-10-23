import sqlite3
from tqdm import tqdm
from sqlalchemy.orm import sessionmaker
from sqlalchemy import create_engine

from photo_organizer.common import DB_PATH, MEDIA_DIRS
from photo_organizer.utils.index_photos import get_photo_files, index_photos_batch
from photo_organizer.db.models import Photo


def index_photos():
    engine = create_engine(f"sqlite:///{DB_PATH}")
    Session = sessionmaker(bind=engine)
    session = Session()

    existing_files = {photo.full_file_path for photo in session.query(Photo).all()}

    files_to_process = []
    for media_dir in MEDIA_DIRS:
        files_to_process.extend([
            str(p) for p in get_photo_files(media_dir)
            if str(p) not in existing_files
        ])

    if not files_to_process:
        print("No new photos to index")
        session.close()
        return

    print(f"Found {len(files_to_process)} photos to index")

    with tqdm(total=len(files_to_process), desc="Indexing Photos", unit="file") as pbar:
        def update_progress(current, total):
            pbar.update(1)

        indexed, errors = index_photos_batch(
            session,
            files_to_process,
            max_workers=8,
            progress_callback=update_progress
        )

    print(f"Indexed {indexed} photos, {errors} errors")
    session.close()


if __name__ == "__main__":
    index_photos()
