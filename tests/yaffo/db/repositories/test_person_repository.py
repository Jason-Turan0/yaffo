"""update_person_embedding: birthdate estimation + per-life-stage medoid gallery."""
import json
from datetime import date, timedelta

import numpy as np
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from yaffo.db import db
from yaffo.db.models import Person, MediaItem, Face, PersonFace, PersonEmbedding, FACE_STATUS_ASSIGNED
from yaffo.domain.compare_utils import serialize_embedding, load_embedding
from yaffo.db.repositories.person_repository import (
    update_person_embedding,
    get_similarity_bounds,
    MAX_REPRESENTATIVE_FACES,
)
from yaffo.domain.compare_utils import DEFAULT_SIMILARITY_FLOOR, DEFAULT_SIMILARITY_CEIL

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


def _add_face(sess, person, photo, vec, age, det_score=None, similarity=None):
    face = Face(
        embedding=serialize_embedding(vec), media_item_id=photo.id, estimated_age=age,
        status=FACE_STATUS_ASSIGNED, det_score=det_score, location_top=0,
        location_right=10, location_bottom=10, location_left=0,
    )
    sess.add(face)
    sess.flush()
    sess.add(PersonFace(person_id=person.id, face_id=face.id, similarity=similarity))
    sess.flush()
    return face


def test_buckets_faces_into_life_stages_and_estimates_birthdate(session):
    person = Person(name="Kid")
    p2010 = MediaItem(year=2010)
    p2020 = MediaItem(year=2020)
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
    p2020 = MediaItem(year=2020)
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
    photo = MediaItem(year=None)  # undated -> birthdate can't be estimated
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
    photo = MediaItem(year=2010)  # all adult
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


def test_collapses_same_day_faces_to_highest_score(session):
    """Many faces from one day collapse to the single highest-confidence detection
    (so a burst can't dominate the medoid), while distinct days each stay
    represented."""
    person = Person(name="Burst", birthdate=date(1990, 1, 1))
    june_a = MediaItem(year=2010, date_taken="2010-06-01T10:00:00")
    june_b = MediaItem(year=2010, date_taken="2010-06-01T10:00:05")  # same day, sharper
    august = MediaItem(year=2010, date_taken="2010-08-15T09:00:00")
    session.add_all([person, june_a, june_b, august])
    session.flush()

    _add_face(session, person, june_a, _unit(1.0), age=20, det_score=0.50)
    june_winner = _add_face(session, person, june_b, _unit(0.99, 0.01), age=20, det_score=0.99)
    august_face = _add_face(session, person, august, _unit(0.0, 1.0), age=20, det_score=0.80)
    session.commit()

    update_person_embedding(person.id, session)
    session.expire_all()
    person = session.get(Person, person.id)

    [stage] = person.stage_embeddings
    included = set(json.loads(stage.included_face_ids))
    assert included == {june_winner.id, august_face.id}  # one per day, the sharper June frame


def test_undated_faces_are_each_kept(session):
    """Faces whose photo has no capture date can't be day-bucketed, so none are
    dropped -- each is its own representative."""
    person = Person(name="NoDates", birthdate=date(1990, 1, 1))
    photo = MediaItem(year=2010)  # no date_taken
    session.add_all([person, photo])
    session.flush()
    f1 = _add_face(session, person, photo, _unit(1.0), age=20, det_score=0.4)
    f2 = _add_face(session, person, photo, _unit(0.0, 1.0), age=20, det_score=0.9)
    session.commit()

    update_person_embedding(person.id, session)
    session.expire_all()
    person = session.get(Person, person.id)

    [stage] = person.stage_embeddings
    assert set(json.loads(stage.included_face_ids)) == {f1.id, f2.id}


def test_caps_at_max_representative_faces_by_score(session):
    """Beyond the daily collapse, a person photographed across thousands of days is
    capped to the MAX_REPRESENTATIVE_FACES sharpest faces."""
    person = Person(name="Prolific", birthdate=date(1990, 1, 1))
    session.add(person)
    session.flush()

    n_days = MAX_REPRESENTATIVE_FACES + 50
    kept_ids = set()
    for i in range(n_days):
        day = date(2010, 1, 1) + timedelta(days=i)
        photo = MediaItem(year=2010, date_taken=f"{day.isoformat()}T10:00:00")
        session.add(photo)
        session.flush()
        # Higher day index -> higher det_score, so the last MAX faces survive.
        face = _add_face(session, person, photo, _unit(1.0), age=20, det_score=i / 1000.0)
        if i >= n_days - MAX_REPRESENTATIVE_FACES:
            kept_ids.add(face.id)
    session.commit()

    update_person_embedding(person.id, session)
    session.expire_all()
    person = session.get(Person, person.id)

    included = {fid for e in person.stage_embeddings for fid in json.loads(e.included_face_ids)}
    assert len(included) == MAX_REPRESENTATIVE_FACES
    assert included == kept_ids


def test_similarity_bounds_falls_back_when_too_few_samples(session):
    """Below the sample floor the percentiles are noisy, so the documented defaults
    are used instead."""
    person = Person(name="Sparse")
    photo = MediaItem(year=2010)
    session.add_all([person, photo])
    session.flush()
    _add_face(session, person, photo, _unit(1.0), age=20, similarity=0.5)
    session.commit()

    assert get_similarity_bounds(session) == (DEFAULT_SIMILARITY_FLOOR, DEFAULT_SIMILARITY_CEIL)


def test_similarity_bounds_reads_percentiles_from_data(session):
    """With enough assignments the band is the low/high percentiles of the actual
    stored similarities, not the hardcoded defaults."""
    person = Person(name="Busy")
    photo = MediaItem(year=2010)
    session.add_all([person, photo])
    session.flush()
    # similarities 0.00, 0.01, ... 0.99 -> p5 ~= 0.05, p95 ~= 0.95.
    for i in range(100):
        _add_face(session, person, photo, _unit(1.0, i), age=20, similarity=i / 100.0)
    session.commit()

    floor, ceil = get_similarity_bounds(session)
    assert floor == pytest.approx(0.05, abs=0.02)
    assert ceil == pytest.approx(0.95, abs=0.02)
