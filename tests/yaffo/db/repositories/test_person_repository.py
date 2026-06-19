"""update_person_embedding: birthdate estimation + per-life-stage medoid gallery."""
from datetime import date

import numpy as np
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from yaffo.db import db
from yaffo.db.models import Person, Photo, Face, PersonFace, PersonEmbedding, FACE_STATUS_ASSIGNED
from yaffo.domain.compare_utils import serialize_embedding, load_embedding
from yaffo.db.repositories.person_repository import update_person_embedding

pytestmark = pytest.mark.unit


@pytest.fixture
def session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    db.metadata.create_all(engine)
    with Session(engine) as sess:
        yield sess


def _unit(*vals) -> np.ndarray:
    v = np.zeros(512, dtype=np.float32)
    for i, x in enumerate(vals):
        v[i] = x
    n = np.linalg.norm(v)
    return v / n if n else v


def _add_face(sess, person, photo, vec, age):
    face = Face(
        embedding=serialize_embedding(vec), photo_id=photo.id, estimated_age=age,
        status=FACE_STATUS_ASSIGNED, location_top=0, location_right=10,
        location_bottom=10, location_left=0,
    )
    sess.add(face)
    sess.flush()
    sess.add(PersonFace(person_id=person.id, face_id=face.id))
    sess.flush()
    return face


def test_buckets_faces_into_life_stages_and_estimates_birthdate(session):
    person = Person(name="Kid")
    p2010 = Photo(year=2010)
    p2020 = Photo(year=2020)
    session.add_all([person, p2010, p2020])
    session.flush()

    # 2010 photo, predicted age 5 -> born ~2005 (child); 2020 photo, age 15 -> teen.
    _add_face(session, person, p2010, _unit(1.0), age=5)
    _add_face(session, person, p2020, _unit(0.0, 1.0), age=15)
    session.commit()

    update_person_embedding(person.id, session)
    session.expire_all()
    person = session.get(Person, person.id)

    assert person.estimated_birthdate == date(2005, 1, 1)  # median(2010-5, 2020-15)
    assert {e.life_stage for e in person.stage_embeddings} == {"child", "teen"}


def test_actual_birthdate_overrides_estimate_for_bucketing(session):
    person = Person(name="P", birthdate=date(2018, 1, 1))  # actual: makes 2020 photo a toddler
    p2020 = Photo(year=2020)
    session.add_all([person, p2020])
    session.flush()
    _add_face(session, person, p2020, _unit(1.0), age=30)  # bogus age; actual birthdate wins
    session.commit()

    update_person_embedding(person.id, session)
    session.expire_all()
    person = session.get(Person, person.id)

    # age ~2 at photo time -> baby, despite the wrong predicted age
    assert {e.life_stage for e in person.stage_embeddings} == {"baby"}


def test_falls_back_to_estimated_age_when_no_birthdate(session):
    """A photo with no date means no birthdate can be derived, so the face's own
    predicted age buckets it (better than dumping it in 'unknown')."""
    person = Person(name="NoDate")
    photo = Photo(year=None)  # undated -> birthdate can't be estimated
    session.add_all([person, photo])
    session.flush()
    _add_face(session, person, photo, _unit(1.0), age=1)  # predicted baby
    session.commit()

    update_person_embedding(person.id, session)
    session.expire_all()
    person = session.get(Person, person.id)

    assert person.estimated_birthdate is None
    assert {e.life_stage for e in person.stage_embeddings} == {"baby"}


def test_medoid_is_a_real_stored_embedding(session):
    person = Person(name="Many", birthdate=date(1990, 1, 1))
    photo = Photo(year=2010)  # all adult
    session.add_all([person, photo])
    session.flush()
    vecs = [_unit(1.0), _unit(0.9, 0.1), _unit(0.95, 0.05), _unit(-1.0)]
    for v in vecs:
        _add_face(session, person, photo, v, age=20)
    session.commit()

    update_person_embedding(person.id, session)
    session.expire_all()
    person = session.get(Person, person.id)

    [stage] = person.stage_embeddings
    medoid = load_embedding(stage.avg_embedding)
    # the medoid is one of the actual face vectors (not an average)
    assert any(np.allclose(medoid, v) for v in vecs)
