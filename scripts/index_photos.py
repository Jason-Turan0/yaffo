import sqlite3
from tqdm import tqdm
from sqlalchemy.orm import sessionmaker
from sqlalchemy import create_engine

from yaffo.common import DB_PATH
from yaffo.routes.utilities.common import get_media_dirs
from yaffo.utils.index_photos import get_photo_files, index_photos_batch
from yaffo.db.models import MediaItem


def index_photos():
    engine = create_engine(f"sqlite:///{DB_PATH}")
    Session = sessionmaker(bind=engine)
    session = Session()

    existing_files = {media_item.full_file_path for media_item in session.query(MediaItem).all()}
    media_dirs = get_media_dirs()
    files_to_process = []
    for media_dir in media_dirs:
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
