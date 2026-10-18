#!/usr/bin/env python3
"""Seed one demo device's golden state, and cross-register a paired peer.

`seed` indexes this device's fixture media (bind-mounted at /data/media), seeds
people/faces, (source only) the Chicago Weekend album, the Florida Trip custom
page, and the file-favorite-kid-photos custom automation, plus a custom theme
on both devices, and generates/loads this device's real P2P identity. `label`
classifies labels (kept in its own process — see cmd_label). `pair` records a
peer as trusted and, on the source device, grants it the folder + album scope.

Must run inside the built yaffo-demo:local image, against the same volumes the
long-running demo-a/demo-b services use (see deploy/demo/README.md), so that
MediaItem.full_file_path/Face.full_file_path match what the running container
will serve, and so face/label embeddings come from the exact model versions
baked into the image. Never run this against a container with
YAFFO_DEMO_MODE=1 — pairing/grant writes are rejected in demo mode by design
(yaffo.runtime_mode.reject_in_demo); override it to "0" for this one-off run.
See deploy/demo/seed-local.sh for the full local orchestration.

The fixture content (from yaffo_ui_tests/test_data) is two different sources:
device A (Bennett) is a synthetic library of generated people and scenes, used
for both UI testing and this demo. Device B (Obama) is real photography from
the Barack Obama Presidential Library/NARA, public domain with a checked-in
attribution record (yaffo_ui_tests/test_data/obama/ATTRIBUTION.md).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# seed_database.py and bennett_face_assignments.json are bind-mounted here
# alongside this script (they aren't part of the shipped image).
sys.path.insert(0, "/mnt")

DISPLAY_NAMES = {"source": "Bennett Family", "receiver": "Obama Family"}

MEDIA_ROOT = Path("/data/media")
IDENTITY_DIR = Path("/identity")
IDENTITY_INFO_PATH = IDENTITY_DIR / "device-id.json"
BENNETT_ASSIGNMENTS_PATH = Path("/mnt/bennett_face_assignments.json")

ALBUM_NAME = "Chicago Weekend"
ALBUM_DESCRIPTION = "Seeded for the public demo"
ALBUM_PHOTOS_PER_FOLDER = 3
# Bennett's real folder names from the synthetic fixture. The folder grant and
# the album deliberately cover different (overlapping) scopes so the walkthrough
# can show they are independent.
GRANTED_FOLDER = "2015_chicago_baby_trip"
ALBUM_FOLDERS = ("2015_chicago_baby_trip", "2021_gulf_beach_trip")

# sharing_download_directory (the route that sets this) is not one of the three
# demo-mode unsafe-method exceptions, so an anonymous receiver visitor can never
# set a download directory themselves — it must already be configured, or every
# pull attempt fails with "Choose a download directory for shared files first."
RECEIVER_DOWNLOAD_DIR = Path("/data/downloads")


def _data_dir() -> Path:
    data_dir = os.environ.get("YAFFO_DATA_DIR")
    if not data_dir:
        print("Error: YAFFO_DATA_DIR environment variable not set", file=sys.stderr)
        sys.exit(1)
    return Path(data_dir)


def _seed_chicago_weekend_album(session, media_root: Path) -> None:
    from yaffo.db.models import MediaItem
    from yaffo.db.repositories import album_repository

    if album_repository.get_album_by_name(session, ALBUM_NAME) is not None:
        print(f"  Album already exists: {ALBUM_NAME}")
        return

    photo_ids: list[int] = []
    for folder in ALBUM_FOLDERS:
        prefix = str(media_root / folder)
        ids = [
            row[0]
            for row in session.query(MediaItem.id)
            .filter(MediaItem.full_file_path.like(f"{prefix}/%"))
            .order_by(MediaItem.full_file_path)
            .limit(ALBUM_PHOTOS_PER_FOLDER)
            .all()
        ]
        photo_ids.extend(ids)
    if not photo_ids:
        print("  Skipped Chicago Weekend album: no matching photos indexed")
        return

    album = album_repository.create_album(session, ALBUM_NAME, description=ALBUM_DESCRIPTION)
    album_repository.add_items(session, album.id, photo_ids)
    print(f"  Seeded album: {ALBUM_NAME} ({len(photo_ids)} photos across {len(ALBUM_FOLDERS)} folders)")


# Mirrors seed_database.SEED_PROFILE_BENNETT/SEED_PROFILE_OBAMA ("bennett"/"obama")
# as plain literals rather than an import: seed_database.py's module-level imports
# pull in onnxruntime/InsightFace, and cmd_pair (a separate process, no indexing)
# should not pay for that just to know these two strings.
SEED_PROFILES = {"source": "bennett", "receiver": "obama"}


def cmd_seed(role: str) -> None:
    from seed_database import (
        BENNETT_PEOPLE,
        OBAMA_PEOPLE,
        index_media_library,
        seed_bennett_face_assignments,
        seed_custom_automations,
        seed_custom_pages,
        seed_custom_themes,
        seed_people,
    )

    from yaffo import themes
    from yaffo.app import create_app
    from yaffo.db import db
    from yaffo.db.models import ApplicationSettings, MediaItem
    from yaffo.db.repositories.media_dir_repository import add_media_dir, get_media_dir_entries
    from yaffo.p2p.identity import load_or_create_identity
    from yaffo.scripts.db.migrate import run_migrations

    data_dir = _data_dir()
    thumbnail_dir = data_dir / "thumbnails"

    # Real migrations, not db.create_all(): the shipped container runs
    # `python -m yaffo` on every boot, which applies pending migrations against
    # `schema_migrations`. Seeding through db.create_all() (the UI-test-sandbox
    # shortcut) would leave that tracking table empty and make the next real
    # boot re-run every migration against tables that already exist.
    run_migrations()

    app = create_app()
    with app.app_context():
        if db.session.query(MediaItem).count() > 0:
            print("  Already seeded (media items present) — skipping content seed")
        else:
            if not db.session.query(ApplicationSettings).filter_by(name="thumbnail_dir").first():
                db.session.add(
                    ApplicationSettings(name="thumbnail_dir", type="str", value=str(thumbnail_dir))
                )
                db.session.commit()
            existing_dir = next(
                (entry for entry in get_media_dir_entries(db.session) if entry.path == MEDIA_ROOT), None
            )
            if existing_dir is None:
                add_media_dir(db.session, str(MEDIA_ROOT))
                db.session.commit()
            print(f"  media_dirs=[{MEDIA_ROOT}] thumbnail_dir={thumbnail_dir}")

            indexed = index_media_library(MEDIA_ROOT, thumbnail_dir)
            print(f"  Indexed {indexed} items")

            if role == "source":
                seed_people(db, BENNETT_PEOPLE, "Bennett")
                seed_bennett_face_assignments(db, MEDIA_ROOT, assignments_path=BENNETT_ASSIGNMENTS_PATH)
                _seed_chicago_weekend_album(db.session, MEDIA_ROOT)
            else:
                seed_people(db, OBAMA_PEOPLE, "Obama")

            # Bennett-only (Florida Trip page, kid-photo-filing automation): the
            # UI-test fixture only defines these for the Bennett profile. The
            # custom theme is profile-agnostic and seeded on both devices.
            seed_custom_automations(db, SEED_PROFILES[role])
            seed_custom_themes(db)
            seed_custom_pages(db, SEED_PROFILES[role])

        if role == "receiver":
            themes.set_theme("neobrutalist")
            print("  Theme set: neobrutalist")

            from yaffo.utils.settings import get_shared_download_dir, set_shared_download_dir

            if get_shared_download_dir(db.session) is None:
                RECEIVER_DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
                set_shared_download_dir(db.session, RECEIVER_DOWNLOAD_DIR)
                print(f"  Download directory set: {RECEIVER_DOWNLOAD_DIR}")

        identity = load_or_create_identity()
        IDENTITY_INFO_PATH.write_text(json.dumps({
            "device_id": identity.device_id,
            "pubkey": identity.public_key_b64,
            "display_name": DISPLAY_NAMES[role],
        }))
        print(f"  Identity ready: {identity.device_id}")


def cmd_label() -> None:
    """Classify labels in their own process. Kept separate from `seed`: loading
    InsightFace (indexing) and CLIP (classification) in the same process pushed
    memory past the container's mem_limit and got OOM-killed. Real demo
    containers never hit this — classify-labels is blocked in demo mode — this
    isolation only matters for golden-state seeding itself."""
    from seed_database import seed_media_labels

    from yaffo.app import create_app
    from yaffo.db import db

    app = create_app()
    with app.app_context():
        seed_media_labels(db)


def cmd_pair(role: str, peer_device_id: str, peer_pubkey: str, peer_display_name: str) -> None:
    from yaffo.app import create_app
    from yaffo.db import db
    from yaffo.db.models import GRANT_SCOPE_ALBUM, GRANT_SCOPE_FOLDER
    from yaffo.db.repositories import album_repository, p2p_repository
    from yaffo.db.repositories.media_dir_repository import get_media_dir_entries

    app = create_app()
    with app.app_context():
        p2p_repository.upsert_trusted_device(db.session, peer_device_id, peer_pubkey, peer_display_name)
        print(f"  Trusted peer: {peer_display_name} ({peer_device_id})")

        if role != "source":
            return  # only the source grants; the receiver just needs the trust row

        media_dir = next(
            (entry for entry in get_media_dir_entries(db.session) if entry.path == MEDIA_ROOT), None
        )
        if media_dir is None:
            raise RuntimeError(f"no media dir registered for {MEDIA_ROOT}; run `seed` first")
        p2p_repository.create_grant(
            db.session, peer_device_id, GRANT_SCOPE_FOLDER,
            media_dir_id=media_dir.id, relative_path=GRANTED_FOLDER,
        )
        album = album_repository.get_album_by_name(db.session, ALBUM_NAME)
        if album is None:
            raise RuntimeError(f"no {ALBUM_NAME!r} album; run `seed` first")
        p2p_repository.create_grant(db.session, peer_device_id, GRANT_SCOPE_ALBUM, album_id=album.id)
        print(f"  Granted {peer_display_name}: folder {GRANTED_FOLDER!r} + album {ALBUM_NAME!r}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    seed_parser = subparsers.add_parser("seed", help="Index fixture media and seed this device's own library")
    seed_parser.add_argument("--role", choices=("source", "receiver"), required=True)

    subparsers.add_parser("label", help="Classify labels (run after `seed`, as a separate process)")

    pair_parser = subparsers.add_parser("pair", help="Trust a peer device and (source only) grant it access")
    pair_parser.add_argument("--role", choices=("source", "receiver"), required=True)
    pair_parser.add_argument("--peer-device-id", required=True)
    pair_parser.add_argument("--peer-pubkey", required=True)
    pair_parser.add_argument("--peer-display-name", required=True)

    args = parser.parse_args()
    if os.environ.get("YAFFO_DEMO_MODE") == "1":
        print(
            "Error: refusing to run with YAFFO_DEMO_MODE=1 "
            "(pairing/grant writes are rejected in demo mode)",
            file=sys.stderr,
        )
        sys.exit(1)

    if args.command == "seed":
        cmd_seed(args.role)
    elif args.command == "label":
        cmd_label()
    else:
        cmd_pair(args.role, args.peer_device_id, args.peer_pubkey, args.peer_display_name)


if __name__ == "__main__":
    main()
