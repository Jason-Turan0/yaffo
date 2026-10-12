"""Fill in `media_items.sha1` for photos indexed before it existed.

Import dedupe compares content hashes, so a source run first hashes whatever the
library is missing. Files are hashed without holding a write lock, then each chunk's
hashes are written in one short transaction (the SQLite concurrency rule).
"""
from __future__ import annotations

import time
from pathlib import Path

from sqlalchemy.orm import Session

from yaffo.db.models import MediaItem, MEDIA_STATUS_INDEXED
from yaffo.sync.files import file_sha1

CHUNK = 200


def missing_count(session: Session) -> int:
    return (session.query(MediaItem.id)
            .filter(MediaItem.sha1.is_(None), MediaItem.status == MEDIA_STATUS_INDEXED).count())


def backfill_sha1(session: Session, time_budget_seconds: float) -> int:
    """Hash un-hashed indexed photos until done or out of time. Returns how many are
    still missing. Files that can't be read are skipped (left NULL)."""
    started = time.monotonic()
    skipped: set[int] = set()
    while time.monotonic() - started < time_budget_seconds:
        query = (session.query(MediaItem.id, MediaItem.full_file_path)
                 .filter(MediaItem.sha1.is_(None), MediaItem.status == MEDIA_STATUS_INDEXED))
        if skipped:
            query = query.filter(MediaItem.id.not_in(skipped))
        rows = query.order_by(MediaItem.id).limit(CHUNK).all()
        session.rollback()  # end the read transaction before the slow, lock-free part
        if not rows:
            break
        hashes: dict[int, str] = {}
        for media_id, path in rows:
            try:
                hashes[media_id] = file_sha1(Path(path))
            except OSError:
                skipped.add(media_id)
        for media_id, digest in hashes.items():
            session.query(MediaItem).filter(MediaItem.id == media_id).update(
                {MediaItem.sha1: digest}, synchronize_session=False)
        session.commit()
    return missing_count(session) - len(skipped)
