"""assign_faces_to_person: the queued half of a manual face assignment.

The route marks faces PROCESSING before queueing the task. A failure used to be
swallowed -- the queue recorded the task as done and the faces stayed PROCESSING
forever, invisible on the Unassigned Faces screen. On failure the task now puts them
back to UNASSIGNED and re-raises so the queue records an error.
"""
import importlib

import pytest
from sqlalchemy import create_engine

from yaffo.background_tasks.utils import SessionFactory, engine as prod_engine
from yaffo.db import db
from yaffo.db.models import (
    FACE_STATUS_ASSIGNED,
    FACE_STATUS_PROCESSING,
    FACE_STATUS_UNASSIGNED,
    Face,
    MediaItem,
    Person,
    PersonFace,
)

pytestmark = pytest.mark.unit

# The tasks package re-exports the task under the module's own name.
mod = importlib.import_module("yaffo.background_tasks.tasks.assign_faces_to_person")


@pytest.fixture
def engine(tmp_path, monkeypatch):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'test.db'}",
        connect_args={'check_same_thread': False},
    )
    db.metadata.create_all(engine)
    SessionFactory.configure(bind=engine)
    monkeypatch.setattr(mod, "emit_event", lambda *_args, **_kwargs: None)
    try:
        yield engine
    finally:
        SessionFactory.remove()
        SessionFactory.configure(bind=prod_engine)
        engine.dispose()


def _seed(person: bool = True) -> tuple[int | None, list[int]]:
    session = SessionFactory()
    try:
        media_item = MediaItem(full_file_path="/photos/a.jpg")
        session.add(media_item)
        session.flush()
        faces = [Face(media_item_id=media_item.id, status=FACE_STATUS_PROCESSING) for _ in range(3)]
        session.add_all(faces)
        person_row = Person(name="Ada") if person else None
        if person_row:
            session.add(person_row)
        session.commit()
        return (person_row.id if person_row else None), [face.id for face in faces]
    finally:
        session.close()


def _statuses(face_ids: list[int]) -> set[str]:
    session = SessionFactory()
    try:
        return {status for (status,) in session.query(Face.status).filter(Face.id.in_(face_ids))}
    finally:
        session.close()


def test_assigns_processing_faces(engine):
    person_id, face_ids = _seed()

    mod.assign_faces_to_person.fn(person_id, face_ids)

    assert _statuses(face_ids) == {FACE_STATUS_ASSIGNED}


def test_failure_releases_faces_and_is_reported(engine):
    _person_id, face_ids = _seed(person=False)

    with pytest.raises(ValueError, match="not found"):
        mod.assign_faces_to_person.fn(999, face_ids)

    assert _statuses(face_ids) == {FACE_STATUS_UNASSIGNED}
    session = SessionFactory()
    try:
        assert session.query(PersonFace).count() == 0
    finally:
        session.close()


def test_failure_after_commit_keeps_the_assignment(engine, monkeypatch):
    person_id, face_ids = _seed()

    def fail(*_args, **_kwargs):
        raise RuntimeError("embedding rebuild failed")

    monkeypatch.setattr(mod, "update_person_embedding", fail)

    with pytest.raises(RuntimeError):
        mod.assign_faces_to_person.fn(person_id, face_ids)

    # The link and status were committed before the rebuild; only PROCESSING
    # faces are released, so the completed assignment stands.
    assert _statuses(face_ids) == {FACE_STATUS_ASSIGNED}
