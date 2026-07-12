#!/usr/bin/env python3
"""One-off backfill: fill in `media_items.orientation`, and rotate the face boxes
of photos indexed before indexing went upright.

Indexing now transposes a photo by its EXIF orientation before face detection, so
boxes and face thumbnails land in upright, as-displayed pixel space — the space the
browser draws in. Photos indexed earlier hold boxes from the *raw* buffer, which for
an EXIF-rotated file point somewhere else entirely (on a portrait iPhone shot, a face
box can land on a chair). This rotates those boxes in place and re-cuts their
thumbnails from the upright image.

Faces, embeddings and person assignments are left alone: no re-detection happens
here, so nothing you have already assigned is lost. The embeddings of those older
faces were computed from sideways crops and are therefore weaker than they should be
— a full re-index would fix that, at the cost of the assignments.

Idempotent: `orientation IS NULL` is exactly the set of rows that predate the change,
so a second run finds nothing to do.

Run:  python -m scripts.backfill_orientation [--dry-run]
"""
import argparse
from pathlib import Path

from yaffo.app import create_app
from yaffo.common import MEDIA_TYPE_PHOTO
from yaffo.db import db
from yaffo.db.models import MediaItem
from yaffo.scripts.db.migrate import run_migrations
from yaffo.utils.image import (
    ORIENTATION_UPRIGHT,
    exif_orientation,
    image_from_path,
    upright_box,
    upright_image_from_path,
)

COMMIT_EVERY = 200
THUMBNAIL_EDGE = 150  # matches index_photos.save_face_thumbnail


def _recut_thumbnail(photo_path: Path, thumbnail_path: Path, box: tuple[int, int, int, int]) -> bool:
    """Re-cut a face thumbnail from the upright image, over the existing file so the
    Face row keeps pointing at it."""
    top, right, bottom, left = box
    try:
        image = upright_image_from_path(photo_path)
        crop = image.crop((left, top, right, bottom))
        crop.thumbnail((THUMBNAIL_EDGE, THUMBNAIL_EDGE))
        crop.save(thumbnail_path, "JPEG")
        return True
    except Exception as error:  # noqa: BLE001 - a bad file shouldn't abort the backfill
        print(f"  ! could not re-cut {thumbnail_path}: {error}")
        return False


def backfill_orientation(dry_run: bool = False) -> None:
    # The app runs migrations at boot; this may be run before that ever happens, and
    # the column it fills in arrives in one. run_migrations is idempotent.
    run_migrations()

    app = create_app()
    with app.app_context():
        pending = (
            db.session.query(MediaItem)
            .filter(
                MediaItem.media_type == MEDIA_TYPE_PHOTO,
                or_(MediaItem.orientation.is_(None), MediaItem.width.is_(None)),
            )
            .all()
        )
        print(f"{len(pending)} photo(s) without a recorded orientation or size.")

        missing = unreadable = rotated_photos = rotated_faces = recut = 0
        for index, media_item in enumerate(pending, start=1):
            photo_path = Path(media_item.full_file_path)
            if not photo_path.exists():
                missing += 1
                continue

            try:
                # image_from_path, not Image.open: HEIC needs the pillow_heif route.
                # (It decodes via pillow_heif, which drops the EXIF block — so HEICs
                # read as upright here, exactly as the /media route serves them.)
                image = image_from_path(photo_path)
                orientation = exif_orientation(image)
                raw_width, raw_height = image.size
            except Exception as error:  # noqa: BLE001 - one bad file shouldn't abort the run
                print(f"  ! could not read {photo_path}: {error}")
                unreadable += 1
                continue

            media_item.orientation = orientation
            if orientation != ORIENTATION_UPRIGHT and media_item.faces:
                rotated_photos += 1
                for face in media_item.faces:
                    if None in (face.location_top, face.location_right,
                                face.location_bottom, face.location_left):
                        continue
                    box = upright_box(
                        top=face.location_top,
                        right=face.location_right,
                        bottom=face.location_bottom,
                        left=face.location_left,
                        orientation=orientation,
                        raw_width=raw_width,
                        raw_height=raw_height,
                    )
                    if not dry_run:
                        face.location_top, face.location_right, face.location_bottom, face.location_left = box
                        if face.full_file_path and _recut_thumbnail(
                                photo_path, Path(face.full_file_path), box):
                            recut += 1
                    rotated_faces += 1

            if not dry_run and index % COMMIT_EVERY == 0:
                db.session.commit()
                print(f"  … {index}/{len(pending)}")

        if dry_run:
            db.session.rollback()
        else:
            db.session.commit()

        print(
            f"{'Would rotate' if dry_run else 'Rotated'} {rotated_faces} face box(es) "
            f"across {rotated_photos} EXIF-rotated photo(s); "
            f"{recut} thumbnail(s) re-cut; "
            f"{missing} file(s) missing on disk, {unreadable} unreadable."
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="report what would change, write nothing")
    backfill_orientation(**vars(parser.parse_args()))
