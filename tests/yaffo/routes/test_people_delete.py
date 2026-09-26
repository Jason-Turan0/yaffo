"""Deleting a person from the people page: the shared delete_person (also the
assistant's and automations' host function) unassigns their faces."""
import pytest

from yaffo.db import db
from yaffo.db.models import FACE_STATUS_ASSIGNED, FACE_STATUS_UNASSIGNED, Face, MediaItem, Person, PersonEmbedding, PersonFace

pytestmark = pytest.mark.unit


def test_delete_person_unassigns_their_faces_and_drops_their_embeddings(client):
    db.session.add_all([
        MediaItem(id=1, full_file_path="/lib/1.jpg"),
        Person(id=1, name="Billy"),
        Person(id=2, name="Bea"),
        Face(id=1, media_item_id=1, status=FACE_STATUS_ASSIGNED),
        Face(id=2, media_item_id=1, status=FACE_STATUS_ASSIGNED),
    ])
    db.session.flush()
    db.session.add_all([PersonFace(person_id=1, face_id=1), PersonFace(person_id=2, face_id=2),
                        PersonEmbedding(person_id=1, life_stage="unknown", avg_embedding=b"x")])
    db.session.commit()

    response = client.post("/people/1/delete")

    assert response.status_code == 302
    db.session.expire_all()
    assert db.session.get(Person, 1) is None
    assert db.session.get(Face, 1).status == FACE_STATUS_UNASSIGNED
    assert [(pf.person_id, pf.face_id) for pf in db.session.query(PersonFace)] == [(2, 2)]
    assert db.session.query(PersonEmbedding).count() == 0
