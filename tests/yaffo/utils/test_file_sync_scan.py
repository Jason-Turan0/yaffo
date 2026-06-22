"""Tests for the media-dir scan: iter_media_scan (streaming generator that drives the
live counter) and scan_media_dirs (its non-streaming wrapper)."""
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from yaffo.db import db
from yaffo.db.models import MediaItem, MEDIA_STATUS_INDEXED
from yaffo.utils.file_sync import (
    MediaScan,
    ORPHAN_MISSING,
    ORPHAN_UNCONFIGURED,
    iter_media_scan,
    scan_media_dirs,
)

pytestmark = pytest.mark.unit


@pytest.fixture
def session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'scan.db'}")
    db.metadata.create_all(engine)
    with Session(engine) as sess:
        yield sess
    engine.dispose()


def _touch(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\xff\xd8\xff")
    return path


@pytest.fixture
def media(tmp_path, session):
    """A media dir + matching index rows covering each case:
    - a.jpg: on disk AND indexed   -> neither unindexed nor orphaned
    - b.jpg: on disk, not in index -> unindexed
    - note.txt: on disk            -> ignored (non-photo)
    - gone.jpg: indexed, not on disk -> orphaned
    """
    media_dir = tmp_path / "organized"
    a = _touch(media_dir / "a.jpg")
    _touch(media_dir / "b.jpg")
    _touch(media_dir / "note.txt")
    gone = media_dir / "gone.jpg"  # never created on disk

    session.add(MediaItem(full_file_path=str(a), status=MEDIA_STATUS_INDEXED))
    session.add(MediaItem(full_file_path=str(gone), status=MEDIA_STATUS_INDEXED))
    session.commit()
    return media_dir


def test_scan_media_dirs_diffs_disk_against_index(session, media):
    scan = scan_media_dirs(session, [media], None)

    assert isinstance(scan, MediaScan)
    assert scan.total_filesystem == 2          # a.jpg, b.jpg (note.txt ignored)
    assert scan.total_imported == 2            # two rows in the DB
    assert scan.total_indexed == 2             # both INDEXED
    assert [u["filename"] for u in scan.unindexed] == ["b.jpg"]
    assert [o["full_path"] for o in scan.orphaned] == [str(media / "gone.jpg")]
    assert scan.orphaned[0]["reason"] == ORPHAN_MISSING


def test_removed_media_dir_orphans_its_photos_even_when_files_exist(tmp_path, session):
    """A photo whose media dir was dropped from config is orphaned even though the
    file still sits on disk -- so syncing removes rows for de-configured dirs."""
    kept = tmp_path / "kept"
    dropped = tmp_path / "dropped"
    in_kept = _touch(kept / "keep.jpg")
    in_dropped = _touch(dropped / "old.jpg")  # file still on disk

    session.add(MediaItem(full_file_path=str(in_kept), status=MEDIA_STATUS_INDEXED))
    session.add(MediaItem(full_file_path=str(in_dropped), status=MEDIA_STATUS_INDEXED))
    session.commit()

    # 'dropped' is no longer in the configured media dirs.
    scan = scan_media_dirs(session, [kept], None)

    assert [(o["full_path"], o["reason"]) for o in scan.orphaned] == [
        (str(in_dropped), ORPHAN_UNCONFIGURED)
    ]


def test_unmounted_media_dir_does_not_orphan_its_photos(tmp_path, session):
    """A configured media dir whose root is gone (e.g. unmounted drive) must NOT
    orphan its photos -- otherwise a transient mount loss would wipe the index."""
    missing_dir = tmp_path / "external"  # configured but never exists on disk
    photo_path = missing_dir / "photo.jpg"

    session.add(MediaItem(full_file_path=str(photo_path), status=MEDIA_STATUS_INDEXED))
    session.commit()

    scan = scan_media_dirs(session, [missing_dir], None)

    assert scan.orphaned == []


def test_iter_media_scan_yields_progress_then_final_scan(session, media):
    events = list(iter_media_scan(session, [media], None, progress_every=1))

    # Progress ints first (one per file walked), then exactly one final MediaScan.
    assert isinstance(events[-1], MediaScan)
    assert all(isinstance(e, int) for e in events[:-1])
    assert len(events[:-1]) >= 1               # at least one progress tick was emitted
    # The running count never exceeds the final filesystem total.
    assert all(0 <= e <= events[-1].total_filesystem for e in events[:-1])


def test_scan_media_dirs_equals_iter_final(session, media):
    wrapped = scan_media_dirs(session, [media], None)
    final = [e for e in iter_media_scan(session, [media], None) if isinstance(e, MediaScan)][-1]
    assert wrapped == final


def test_empty_media_dirs(session):
    scan = scan_media_dirs(session, [], None)
    assert scan.total_filesystem == 0
    assert scan.unindexed == []
