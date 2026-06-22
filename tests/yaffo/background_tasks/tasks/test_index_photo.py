"""Regression: index_photo_task replaces a photo's faces on a re-run instead of
accumulating duplicates.

A task requeued after a host crash is re-run from scratch (at-least-once), so a
replay must converge to the same faces, and the superseded thumbnail crops must be
unlinked. Face thumbnail paths carry a uuid, so the Face.full_file_path unique
constraint can't catch a re-insert -- without the clear-then-insert guard a replay
would silently double every face.
"""
import uuid
from pathlib import Path

import numpy as np
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from yaffo.db import db
from yaffo.db.models import Job, MediaItem, Face, JOB_STATUS_RUNNING, MEDIA_STATUS_INDEXED
from yaffo.background_tasks.config import task_queue
from yaffo.background_tasks.utils import SessionFactory, engine as prod_engine
import yaffo.background_tasks.tasks.index_photo as index_photo_mod
from yaffo.background_tasks.tasks.index_photo import index_photo_task

pytestmark = pytest.mark.unit


def _fake_index_photo_factory(thumbnail_dir: Path):
    """Stand in for the real (dlib-backed) index_photo: write a real crop file under
    a fresh uuid name each call -- exactly the non-deterministic path that defeats
    the unique constraint -- and return one face pointing at it."""
    def fake_index_photo(path: Path, thumb_dir: Path) -> dict:
        thumb = thumbnail_dir / f"face_{path.stem}_{uuid.uuid4().hex[:8]}.jpg"
        thumb.write_bytes(b"\xff\xd8\xff")
        return {
            "faces_data": [{
                "embedding": np.zeros(512, dtype=np.float32),
                "full_file_path": str(thumb),
                "location_top": 0, "location_right": 10,
                "location_bottom": 10, "location_left": 0,
                "estimated_age": 30, "gender": "M", "det_score": 0.9,
            }],
            "latitude": None, "longitude": None, "location_name": None,
            "date_taken": None, "year": None, "month": None, "device": None,
        }
    return fake_index_photo


@pytest.fixture
def db_for_index(tmp_path, monkeypatch):
    """A temp DB bound to the global SessionFactory, with the face-detection leaf and
    the thumbnail-dir lookup stubbed so the task runs without dlib."""
    engine = create_engine(
        f"sqlite:///{tmp_path / 'test.db'}",
        connect_args={'check_same_thread': False},
    )
    db.metadata.create_all(engine)
    SessionFactory.configure(bind=engine)
    task_queue.immediate = True  # run the queue-wrapped task inline, not enqueued

    thumbnail_dir = tmp_path / "thumbs"
    thumbnail_dir.mkdir()
    monkeypatch.setattr(index_photo_mod, "get_current_thumbnail_dir", lambda: thumbnail_dir)
    monkeypatch.setattr(index_photo_mod, "index_photo", _fake_index_photo_factory(thumbnail_dir))

    try:
        yield engine, thumbnail_dir
    finally:
        task_queue.immediate = False
        SessionFactory.remove()
        SessionFactory.configure(bind=prod_engine)
        engine.dispose()


def test_reindex_replaces_faces_not_duplicates(db_for_index, tmp_path):
    engine, thumbnail_dir = db_for_index
    photo_file = tmp_path / "photo.jpg"
    photo_file.write_bytes(b"\xff\xd8\xff")
    job_id = "job-1"

    with Session(engine) as s:
        s.add(Job(id=job_id, name="index_photos", status=JOB_STATUS_RUNNING, task_count=1))
        s.add(MediaItem(full_file_path=str(photo_file)))
        s.commit()

    # First index pass.
    index_photo_task(job_id, [str(photo_file)])

    with Session(engine) as s:
        media_item = s.query(MediaItem).one()
        faces = s.query(Face).all()
        assert len(faces) == 1
        assert media_item.status == MEDIA_STATUS_INDEXED
        first_thumb = Path(faces[0].full_file_path)
    assert first_thumb.exists()

    # Replay the same task -- simulates a requeue after a host crash.
    index_photo_task(job_id, [str(photo_file)])

    with Session(engine) as s:
        faces = s.query(Face).all()
        assert len(faces) == 1                  # replaced, not doubled
        second_thumb = Path(faces[0].full_file_path)

    assert second_thumb.exists()
    assert second_thumb != first_thumb          # a fresh crop was written
    assert not first_thumb.exists()             # the superseded crop was unlinked
    assert list(thumbnail_dir.glob("*.jpg")) == [second_thumb]  # exactly one crop left
