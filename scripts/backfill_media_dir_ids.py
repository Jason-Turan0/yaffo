#!/usr/bin/env python3
"""One-off backfill: migrate the `media_dirs` setting to the {id, path} schema.

Older DBs stored media_dirs as a list of path strings; this assigns a stable uuid4
to each so the automation host can address media dirs by guid (and scripts can move
files between them). Idempotent: entries already in {id, path} form are left as-is.

Run:  python -m scripts.backfill_media_dir_ids
"""
import json
import uuid

from yaffo.app import create_app
from yaffo.db import db
from yaffo.db.models import ApplicationSettings


def backfill_media_dir_ids() -> None:
    app = create_app()
    with app.app_context():
        setting = db.session.query(ApplicationSettings).filter_by(name="media_dirs").first()
        if setting is None or not setting.value:
            print("No media_dirs setting to backfill.")
            return

        migrated, changed = [], False
        for entry in json.loads(setting.value):
            if isinstance(entry, dict) and entry.get("id") and entry.get("path"):
                migrated.append(entry)
            else:
                path = entry if isinstance(entry, str) else entry.get("path", "")
                migrated.append({"id": str(uuid.uuid4()), "path": path})
                changed = True

        if changed:
            setting.value = json.dumps(migrated)
            db.session.commit()
        print(f"media_dirs ({'migrated' if changed else 'already current'}): {migrated}")


if __name__ == "__main__":
    backfill_media_dir_ids()