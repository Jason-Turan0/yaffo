#!/usr/bin/env python3
"""
Seed the test database with photos from the test_data directory.

Usage:
    python seed_database.py

Requires YAFFO_DATA_DIR environment variable to be set.
"""

import json
import os
import sys
from pathlib import Path

from yaffo.db.models import Tag, Face, FACE_STATUS_UNASSIGNED

# Add yaffo project to path
YAFFO_PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(YAFFO_PROJECT_ROOT))


def seed_database() -> int:
    """Index test photos and seed the database. Returns count of photos indexed."""
    data_dir = os.environ.get("YAFFO_DATA_DIR")
    if not data_dir:
        print("Error: YAFFO_DATA_DIR environment variable not set")
        sys.exit(1)

    data_dir = Path(data_dir)
    photos_dir = data_dir / "organized"
    thumbnail_dir = data_dir / "thumbnails"

    from yaffo.app import create_app
    from yaffo.db import db
    from yaffo.db.models import ApplicationSettings, Photo
    from yaffo.utils.index_photos import index_photo

    app = create_app()
    with app.app_context():
        db.create_all()

        # Seed application settings
        thumbnail_setting = ApplicationSettings(
            name="thumbnail_dir",
            type="str",
            value=str(thumbnail_dir),
        )
        db.session.add(thumbnail_setting)

        media_dirs_setting = ApplicationSettings(
            name="media_dirs",
            type="json",
            value=json.dumps([str(photos_dir)]),
        )
        db.session.add(media_dirs_setting)
        db.session.commit()
        print(f"  Created settings: thumbnail_dir={thumbnail_dir}")
        print(f"  Created settings: media_dirs=[{photos_dir}]")

        # Index photos
        indexed_count = 0
        for photo_path in photos_dir.glob("*.jpg"):
            try:
                result = index_photo(photo_path, thumbnail_dir)
                if result:
                    photo = Photo(
                        full_file_path=str(photo_path),
                        date_taken=result.get("date_taken"),
                        year= result.get("year"),
                        month=result.get("month"),
                        latitude=result.get("latitude"),
                        longitude=result.get("longitude"),
                        location_name=result.get("location_name"),
                        status="indexed",
                    )
                    db.session.add(photo)
                    db.session.flush()
                    tags = result["tags"]
                    for tag_data in tags:
                        tag = Tag(
                            photo_id=photo.id,
                            tag_name=tag_data['tag_name'],
                            tag_value=tag_data['tag_value']
                        )
                        db.session.add(tag)
                    faces_data = result["faces_data"]
                    for face_data in faces_data:
                        face = Face(
                            embedding=face_data['embedding'].tobytes(),
                            full_file_path=face_data['full_file_path'],
                            status=FACE_STATUS_UNASSIGNED,
                            photo_id=photo.id,
                            location_top=face_data['location_top'],
                            location_right=face_data['location_right'],
                            location_bottom=face_data['location_bottom'],
                            location_left=face_data['location_left']
                        )
                        db.session.add(face)

                    print(f"  Indexed: {photo_path.name}")
                    indexed_count += 1
            except Exception as e:
                print(f"  Error indexing {photo_path.name}: {e}")

        db.session.commit()
        total = db.session.query(Photo).count()
        print(f"  Total photos in database: {total}")

        return indexed_count


if __name__ == "__main__":
    seed_database()