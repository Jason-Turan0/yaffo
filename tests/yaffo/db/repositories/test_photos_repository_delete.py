"""Repository test for photos_repository.delete_photos — it removes a photo and all
its dependents (tags, labels, faces, people_face links) in one transaction, since
SQLite FK cascade is off, and returns the face thumbnail paths for the caller to unlink."""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from yaffo.db import db
from yaffo.db.models import (
    ClassificationLabel,
    Face,
    Person,
    PersonFace,
    MediaItem,
    MediaLabel,
    Tag,
)
from yaffo.db.repositories import photos_repository as repo

pytestmark = pytest.mark.unit


@pytest.fixture
def session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    db.metadata.create_all(engine)
    with Session(engine) as sess:
        yield sess
    engine.dispose()


def test_delete_photos_removes_photo_and_all_dependents(session):
    photo = MediaItem(full_file_path="/lib/a.jpg")
    keep = MediaItem(full_file_path="/lib/keep.jpg")
    person = Person(name="Alice")
    label = ClassificationLabel(name="beach")
    session.add_all([photo, keep, person, label])
    session.flush()

    face = Face(media_item_id=photo.id, full_file_path="/thumbs/face1.jpg")
    session.add(face)
    session.flush()
    session.add_all([
        Tag(media_item_id=photo.id, tag_name="trip", tag_value="maui"),
        MediaLabel(media_item_id=photo.id, label_id=label.id, confidence=0.9),
        PersonFace(person_id=person.id, face_id=face.id),
    ])
    session.commit()
    # Capture ids before the bulk delete (the ORM objects expire afterwards).
    photo_id, keep_id, person_id, label_id, face_id = photo.id, keep.id, person.id, label.id, face.id

    thumbnails = repo.delete_photos(session, [photo_id])

    assert thumbnails == ["/thumbs/face1.jpg"]
    assert session.get(MediaItem, photo_id) is None
    assert session.query(Face).filter_by(media_item_id=photo_id).count() == 0
    assert session.query(Tag).filter_by(media_item_id=photo_id).count() == 0
    assert session.query(MediaLabel).filter_by(media_item_id=photo_id).count() == 0
    assert session.query(PersonFace).filter_by(face_id=face_id).count() == 0
    # Untouched: the other photo, and shared rows (person, vocabulary label).
    assert session.get(MediaItem, keep_id) is not None
    assert session.get(Person, person_id) is not None
    assert session.get(ClassificationLabel, label_id) is not None


def test_delete_photos_empty_is_noop(session):
    assert repo.delete_photos(session, []) == []


def test_get_photo_ids_for_faces_resolves_distinct_photos(session):
    p1, p2 = MediaItem(full_file_path="/lib/1.jpg"), MediaItem(full_file_path="/lib/2.jpg")
    session.add_all([p1, p2])
    session.flush()
    f1 = Face(media_item_id=p1.id, full_file_path="/t/1.jpg")
    f2 = Face(media_item_id=p1.id, full_file_path="/t/2.jpg")  # second face on the same photo
    f3 = Face(media_item_id=p2.id, full_file_path="/t/3.jpg")
    session.add_all([f1, f2, f3])
    session.commit()

    ids = repo.get_photo_ids_for_faces(session, [f1.id, f2.id, f3.id])

    assert set(ids) == {p1.id, p2.id}  # distinct, two faces on p1 collapse to one
    assert repo.get_photo_ids_for_faces(session, []) == []
