#!/usr/bin/env python3
"""
Seed a minimal PEER database for the sharing UI tests.

The peer instance (instance B in specs/sharing.yaml) pulls shared files; it
does not serve a library. It needs the schema and a thumbnail dir — no media,
no indexing, no model downloads, so this stays fast enough to run on every
sandbox start.

Deliberately NO download directory: the UI can set one but never clear one
(the form rejects an empty value), so the "gallery without a download
directory" scenario must run before any test sets it. The sharing tests set
the directory through the UI to a path they control.

Requires YAFFO_DATA_DIR environment variable to be set (the peer's own dir).
"""

import os
import sys
from pathlib import Path

YAFFO_PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(YAFFO_PROJECT_ROOT))


def seed_peer_database() -> None:
    data_dir = os.environ.get("YAFFO_DATA_DIR")
    if not data_dir:
        print("Error: YAFFO_DATA_DIR environment variable not set")
        sys.exit(1)
    data_dir = Path(data_dir)
    thumbnail_dir = data_dir / "thumbnails"
    thumbnail_dir.mkdir(parents=True, exist_ok=True)

    from yaffo.app import create_app
    from yaffo.db import db
    from yaffo.db.models import ApplicationSettings

    app = create_app()
    with app.app_context():
        db.create_all()
        db.session.add(ApplicationSettings(name="thumbnail_dir", type="str", value=str(thumbnail_dir)))
        db.session.commit()
        print(f"  Peer seeded: thumbnail_dir={thumbnail_dir} (no download dir — tests set it via the UI)")


if __name__ == "__main__":
    seed_peer_database()
