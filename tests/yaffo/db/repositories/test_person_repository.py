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
    recompute_person_similarities,
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


def _add_face(sess, person, media_item, vec, age, det_score=None, similarity=None):
    face = Face(
        embedding=serialize_embedding(vec), media_item_id=media_item.id, estimated_age=age,
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
    media_item = MediaItem(year=None)  # undated -> birthdate can't be estimated
    session.add_all([person, media_item])
    session.flush()
    _add_face(session, person, media_item, _unit(1.0), age=1)  # predicted baby
    session.commit()

    update_person_embedding(person.id, session)
    session.expire_all()
    person = session.get(Person, person.id)

    assert person.estimated_birthdate is None
    assert {e.life_stage for e in person.stage_embeddings} == {"baby"}


def test_medoid_is_a_real_stored_embedding(session):
    person = Person(name="Many", birthdate=date(1990, 1, 1))
    media_item = MediaItem(year=2010)  # all adult
    session.add_all([person, media_item])
    session.flush()
    vecs = [_unit(1.0), _unit(0.9, 0.1), _unit(0.95, 0.05), _unit(-1.0)]
    for v in vecs:
        _add_face(session, person, media_item, v, age=20)
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
    media_item = MediaItem(year=2010)  # no date_taken
    session.add_all([person, media_item])
    session.flush()
    f1 = _add_face(session, person, media_item, _unit(1.0), age=20, det_score=0.4)
    f2 = _add_face(session, person, media_item, _unit(0.0, 1.0), age=20, det_score=0.9)
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
        media_item = MediaItem(year=2010, date_taken=f"{day.isoformat()}T10:00:00")
        session.add(media_item)
        session.flush()
        # Higher day index -> higher det_score, so the last MAX faces survive.
        face = _add_face(session, person, media_item, _unit(1.0), age=20, det_score=i / 1000.0)
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
    media_item = MediaItem(year=2010)
    session.add_all([person, media_item])
    session.flush()
    _add_face(session, person, media_item, _unit(1.0), age=20, similarity=0.5)
    session.commit()

    assert get_similarity_bounds(session) == (DEFAULT_SIMILARITY_FLOOR, DEFAULT_SIMILARITY_CEIL)


def test_similarity_bounds_reads_percentiles_from_data(session):
    """With enough assignments the band is the low/high percentiles of the actual
    stored similarities, not the hardcoded defaults."""
    person = Person(name="Busy")
    media_item = MediaItem(year=2010)
    session.add_all([person, media_item])
    session.flush()
    # similarities 0.00, 0.01, ... 0.99 -> p5 ~= 0.05, p95 ~= 0.95.
    for i in range(100):
        _add_face(session, person, media_item, _unit(1.0, i), age=20, similarity=i / 100.0)
    session.commit()

    floor, ceil = get_similarity_bounds(session)
    assert floor == pytest.approx(0.05, abs=0.02)
    assert ceil == pytest.approx(0.95, abs=0.02)


# PersonFace.similarity is a *cache* of how much a face looks like its person, and the
# person it's measured against moves every time the gallery is rebuilt. These pin the
# rescore that keeps it meaning the same thing as a freshly computed score.

def _similarities(sess, person) -> dict[int, float | None]:
    return {
        pf.face_id: pf.similarity
        for pf in sess.query(PersonFace).filter_by(person_id=person.id).all()
    }


def test_rescores_every_face_against_the_current_gallery(session):
    person = Person(name="Ada")
    media_item = MediaItem(full_file_path="/p.jpg", date_taken="2020-01-01 10:00:00", year=2020)
    session.add_all([person, media_item])
    session.flush()
    # A face that IS the person, and one pointing elsewhere. Both arrive with a stale
    # score, as an assignment would have written before the gallery was rebuilt.
    same = _add_face(session, person, media_item, _unit(1, 0), age=30, similarity=0.01)
    other = _add_face(session, person, media_item, _unit(0, 1), age=30, similarity=0.99)
    session.commit()

    update_person_embedding(person.id, session)

    scores = _similarities(session, person)
    assert scores[same.id] > scores[other.id]
    assert scores[same.id] == pytest.approx(1.0, abs=1e-6)   # it is the medoid
    assert scores[other.id] == pytest.approx(0.0, abs=1e-6)  # orthogonal to it


def test_a_person_with_no_gallery_scores_nothing_rather_than_guessing(session):
    """The old cold-start path scored a person's first batch against its own mean —
    every face came back a strong match for someone it had never been compared to.
    With no gallery there is no answer, and NULL says so."""
    person = Person(name="Ada")
    media_item = MediaItem(full_file_path="/p.jpg")
    session.add_all([person, media_item])
    session.flush()
    face = Face(embedding=None, media_item_id=media_item.id, status=FACE_STATUS_ASSIGNED)
    session.add(face)
    session.flush()
    session.add(PersonFace(person_id=person.id, face_id=face.id, similarity=0.87))
    session.commit()

    update_person_embedding(person.id, session)  # no embeddings -> no gallery

    assert _similarities(session, person) == {face.id: None}


def test_only_missing_leaves_existing_scores_alone(session):
    """When the gallery didn't move, the stored scores are still valid — so a rescore
    limited to the unscored rows must not touch them (this is what keeps assigning one
    face to a 12k-face person cheap)."""
    person = Person(name="Ada")
    media_item = MediaItem(full_file_path="/p.jpg", date_taken="2020-01-01 10:00:00", year=2020)
    session.add_all([person, media_item])
    session.flush()
    scored = _add_face(session, person, media_item, _unit(1, 0), age=30, similarity=0.42)
    unscored = _add_face(session, person, media_item, _unit(1, 0), age=30, similarity=None)
    session.commit()
    update_person_embedding(person.id, session)   # builds the gallery, scores both
    session.query(PersonFace).filter_by(face_id=scored.id).update({"similarity": 0.42})
    session.query(PersonFace).filter_by(face_id=unscored.id).update({"similarity": None})
    session.commit()

    written = recompute_person_similarities(session, person.id, only_missing=True)

    scores = _similarities(session, person)
    assert written == 1
    assert scores[scored.id] == pytest.approx(0.42)          # untouched
    assert scores[unscored.id] == pytest.approx(1.0, abs=1e-6)  # filled in


def test_rescore_survives_a_person_whose_faces_were_all_unassigned(session):
    person = Person(name="Ada")
    media_item = MediaItem(full_file_path="/p.jpg", date_taken="2020-01-01 10:00:00", year=2020)
    session.add_all([person, media_item])
    session.flush()
    face = _add_face(session, person, media_item, _unit(1, 0), age=30, similarity=0.9)
    session.commit()
    update_person_embedding(person.id, session)

    # The face goes away (unassigned/re-indexed); the person has no gallery left.
    session.query(PersonFace).filter_by(face_id=face.id).delete()
    session.commit()
    update_person_embedding(person.id, session)

    assert session.get(Person, person.id).avg_embedding is None
    assert _similarities(session, person) == {}


def test_scores_a_face_against_its_own_life_stage_not_the_best_matching_one(session):
    """A person at 4 and at 40 are nearly orthogonal in embedding space, so a face is
    only meaningfully compared to the medoid of the stage it belongs to.

    This is the case where per-stage and best-over-all-stages disagree: a baby photo
    that happens to resemble the adult medoid more than the baby one. Scoring it against
    the adult medoid would flatter it for looking like the wrong person-at-the-wrong-age.
    """
    person = Person(name="Ada", birthdate=date(2000, 1, 1))
    baby_photo = MediaItem(full_file_path="/baby.jpg", date_taken="2001-01-01 10:00:00", year=2001)
    adult_photo = MediaItem(full_file_path="/adult.jpg", date_taken="2030-01-01 10:00:00", year=2030)
    session.add_all([person, baby_photo, adult_photo])
    session.flush()

    # The gallery: one medoid per stage, deliberately orthogonal to each other.
    _add_face(session, person, baby_photo, _unit(1, 0), age=1)     # baby stage medoid
    _add_face(session, person, adult_photo, _unit(0, 1), age=30)   # adult stage medoid
    session.commit()
    update_person_embedding(person.id, session)

    # Now a baby-stage face (2001 photo) that leans toward the ADULT medoid.
    odd_one = _add_face(session, person, baby_photo, _unit(0.2, 0.98), age=1)
    session.commit()

    recompute_person_similarities(session, person.id)

    score = _similarities(session, person)[odd_one.id]
    # Scored against the baby medoid it belongs to (~0.2), NOT the adult medoid it
    # happens to resemble (~0.98), which is what max-over-stages would have given it.
    assert score == pytest.approx(0.2, abs=0.02)


def test_falls_back_to_the_overall_medoid_for_a_stage_with_no_faces(session):
    """The person has no senior faces, so there's no senior medoid to score against —
    their overall medoid stands in rather than the face going unscored."""
    person = Person(name="Ada", birthdate=date(1950, 1, 1))
    adult_photo = MediaItem(full_file_path="/adult.jpg", date_taken="1980-01-01 10:00:00", year=1980)
    senior_photo = MediaItem(full_file_path="/senior.jpg", date_taken="2020-01-01 10:00:00", year=2020)
    session.add_all([person, adult_photo, senior_photo])
    session.flush()
    _add_face(session, person, adult_photo, _unit(1, 0), age=30)  # the only stage they have
    session.commit()
    update_person_embedding(person.id, session)

    stages = {stage.life_stage for stage in session.get(Person, person.id).stage_embeddings}
    assert stages == {"adult"}  # guard: no senior medoid exists

    senior_face = _add_face(session, person, senior_photo, _unit(1, 0), age=70)
    session.commit()

    recompute_person_similarities(session, person.id)

    # Scored against the overall medoid (which here is the adult face) rather than NULL.
    assert _similarities(session, person)[senior_face.id] == pytest.approx(1.0, abs=1e-6)
