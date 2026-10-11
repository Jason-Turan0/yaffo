"""Give a UI-created Flickr sync a prior consent state for republish coverage."""
from __future__ import annotations

import sys
from dataclasses import replace
from datetime import datetime, timezone

from yaffo.app import create_app
from yaffo.db import db
from yaffo.db.models import Album
from yaffo.db.repositories import automation_repository, sync_repository
from yaffo.sync import setup
from yaffo.sync.consent import consent_hash
from yaffo.sync.schemas import RUNTIME_READY, Selection, SELECTION_ALBUM


def main() -> None:
    slug = sys.argv[1]
    app = create_app()
    with app.app_context():
        automation = automation_repository.get_by_slug(db.session, slug)
        if automation is None or not automation.is_sync:
            raise SystemExit(f"No sync exists for slug {slug}")
        album = db.session.query(Album).order_by(Album.id).first()
        if album is None:
            raise SystemExit("The UI-test seed has no album to select")
        column = sync_repository.load_sync(automation)
        config = replace(column.config, selection=Selection(kind=SELECTION_ALBUM, album_id=album.id),
                         consent_hash=None, consented_at=None)
        adapter = setup.adapter_of(db.session, automation)
        config = replace(config, consent_hash=consent_hash(config, adapter, automation.published_code),
                         consented_at=datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"))
        sync_repository.save_config(automation, config)
        sync_repository.save_runtime(automation, replace(column.runtime, status=RUNTIME_READY, pause_code=None))
        automation.enabled = True
        db.session.commit()


if __name__ == "__main__":
    main()
