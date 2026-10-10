"""Fixtures for the sync engine tests: a real SQLite file, a library folder registered
as a media dir, real JPEG files with capture dates, and an OS trash that's a folder."""
import json
import shutil
from pathlib import Path

import pytest
from PIL import Image
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from yaffo.db import db
from yaffo.db.models import (
    Album, AlbumItem, ApplicationSettings, MediaItem, SyncAdapter,
    MEDIA_STATUS_INDEXED, SYNC_HANDLER_FOLDER_SOURCE, SYNC_HANDLER_FOLDER_TARGET,
)
from yaffo.sync import folder_target, source_run
from yaffo.sync.run_common import RunContext

MEDIA_DIR_ID = "6f1c0000-0000-4000-8000-000000000001"
EXIF_DATETIME_ORIGINAL = 36867
EXIF_IFD = 0x8769


def write_jpeg(path: Path, taken: str | None = "2024:07:04 18:22:10", color=(200, 30, 30),
               size=(64, 48)) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", size, color)
    exif = Image.Exif()
    if taken:
        exif.get_ifd(EXIF_IFD)[EXIF_DATETIME_ORIGINAL] = taken
        exif[306] = taken
    image.save(path, format="JPEG", exif=exif.tobytes())
    return path


@pytest.fixture
def session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    db.metadata.create_all(engine)
    with Session(engine) as sess:
        sess.add_all([
            SyncAdapter(id=1, slug="builtin.folder_source", name="Folder", role="source", provider="local",
                        is_preset=True, handler=SYNC_HANDLER_FOLDER_SOURCE, grants=[], doc_domains=[]),
            SyncAdapter(id=2, slug="builtin.folder_target", name="Folder", role="target", provider="local",
                        is_preset=True, handler=SYNC_HANDLER_FOLDER_TARGET, grants=[], doc_domains=[]),
        ])
        sess.commit()
        yield sess
    engine.dispose()


@pytest.fixture
def library(tmp_path, session) -> Path:
    root = tmp_path / "Pictures"
    root.mkdir()
    thumbnails = tmp_path / "thumbs"
    thumbnails.mkdir()
    session.add(ApplicationSettings(name="media_dirs", type="json",
                                    value=json.dumps([{"id": MEDIA_DIR_ID, "path": str(root)}])))
    session.add(ApplicationSettings(name="thumbnail_dir", type="string", value=str(thumbnails)))
    session.commit()
    return root


@pytest.fixture
def add_photo(session, library):
    def add(name: str, taken: str | None = "2024:07:04 18:22:10", date_taken: str | None = "2024-07-04T18:22:10",
            **kwargs) -> MediaItem:
        path = write_jpeg(library / name, taken, **kwargs)
        media = MediaItem(full_file_path=str(path), status=MEDIA_STATUS_INDEXED, date_taken=date_taken)
        session.add(media)
        session.commit()
        return media
    return add


@pytest.fixture
def album(session):
    def make(name: str, media: list[MediaItem]) -> Album:
        row = Album(name=name)
        session.add(row)
        session.flush()
        for position, item in enumerate(media):
            session.add(AlbumItem(album_id=row.id, media_item_id=item.id, position=position))
        session.commit()
        return row
    return make


@pytest.fixture
def trash(tmp_path, monkeypatch) -> Path:
    """send2trash moves files here instead of the OS trash."""
    bin_dir = tmp_path / "trash"
    bin_dir.mkdir()

    def fake_send2trash(path):
        source = Path(path)
        shutil.move(str(source), str(bin_dir / f"{len(list(bin_dir.iterdir()))}-{source.name}"))

    monkeypatch.setattr(folder_target.send2trash, "send2trash", fake_send2trash)
    monkeypatch.setattr(source_run.send2trash, "send2trash", fake_send2trash)
    return bin_dir


@pytest.fixture(autouse=True)
def no_watcher_store(monkeypatch):
    """The watcher-suppression ledger lives in the queue DB; record writes in memory."""
    recorded: list[str] = []
    monkeypatch.setattr(source_run, "record_self_write", lambda path: recorded.append(str(path)))
    return recorded


@pytest.fixture(autouse=True)
def no_events(monkeypatch):
    emitted: list[tuple[str, dict]] = []
    monkeypatch.setattr(source_run, "emit_event", lambda event, payload: emitted.append((event, payload)))
    return emitted


@pytest.fixture
def run_context(tmp_path):
    indexed: list[list[str]] = []
    reindexed: list[list[int]] = []
    context = RunContext(
        job_id=None,
        index_files=lambda session, paths, automation_id: indexed.append(list(paths)),
        reindex_items=lambda session, ids: reindexed.append(list(ids)),
        scratch_dir=tmp_path,
    )
    context.indexed = indexed
    context.reindexed = reindexed
    return context
