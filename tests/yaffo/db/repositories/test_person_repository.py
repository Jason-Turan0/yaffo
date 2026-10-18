"""update_person_embedding: birthdate estimation + per-life-stage medoid gallery."""
import json
from datetime import date, timedelta

import numpy as np
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from yaffo.db import db
from yaffo.db.models import (
    FACE_STATUS_ASSIGNED,
    FACE_STATUS_IGNORED,
    FACE_STATUS_PROCESSING,
    FACE_STATUS_UNASSIGNED,
    Face,
    MediaItem,
    Person,
    PersonEmbedding,
    PersonFace,
)
from yaffo.domain.compare_utils import serialize_embedding, load_embedding
from yaffo.db.repositories.person_repository import (
    bulk_link_faces_to_people,
    update_person_embedding,
    likely_misassigned_faces,
    unmatched_faces,
    recompute_person_similarities,
    MAX_REPRESENTATIVE_FACES,
)

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


def _stage_faces(session, person) -> dict[str, list[int]]:
    return {e.life_stage: json.loads(e.included_face_ids) for e in session.get(Person, person.id).stage_embeddings}


def test_faces_from_before_the_birth_year_stay_out_of_the_gallery(session):
    """A 2016 photo assigned to someone born in 2019 is someone else. It used to fall
    back to its predicted age (17) and become the person's entire "teen" medoid."""
    person = Person(name="Nathan", birthdate=date(2019, 9, 6))
    p2016, p2021 = MediaItem(year=2016), MediaItem(year=2021)
    session.add_all([person, p2016, p2021])
    session.flush()
    stray = _add_face(session, person, p2016, _unit(0.0, 1.0), age=17)
    kids = [_add_face(session, person, p2021, _unit(1.0, 0.01 * i), age=2) for i in range(3)]
    session.commit()

    update_person_embedding(person.id, session)
    session.expire_all()

    assert _stage_faces(session, person) == {"baby": [face.id for face in kids]}
    # The overall medoid is built without it too (here: one of the baby faces).
    assert load_embedding(session.get(Person, person.id).avg_embedding)[1] < 0.5
    # It is still scored (against the overall medoid), so it shows up as a poor fit.
    assert _similarities(session, person)[stray.id] < 0.1


def test_a_birthdate_that_predates_nothing_builds_from_every_face(session):
    """Every face before the birth year means the birthdate is the likelier error:
    bucket by predicted age rather than leave the person with no gallery."""
    person = Person(name="Typo", birthdate=date(2030, 1, 1))
    p2020 = MediaItem(year=2020)
    session.add_all([person, p2020])
    session.flush()
    faces = [_add_face(session, person, p2020, _unit(1.0, 0.01 * i), age=1) for i in range(2)]
    session.commit()

    update_person_embedding(person.id, session)
    session.expire_all()

    assert _stage_faces(session, person) == {"baby": [face.id for face in faces]}


def test_a_stage_needs_three_faces_once_another_stage_has_them(session):
    person = Person(name="Pat", birthdate=date(1990, 1, 28))
    p1991, p2020 = MediaItem(year=1991), MediaItem(year=2020)
    session.add_all([person, p1991, p2020])
    session.flush()
    babies = [_add_face(session, person, p1991, _unit(1.0, 0.01 * i), age=1) for i in range(3)]
    _add_face(session, person, p2020, _unit(0.0, 1.0), age=30)  # a lone "adult" face
    session.commit()

    update_person_embedding(person.id, session)
    session.expire_all()

    assert _stage_faces(session, person) == {"baby": [face.id for face in babies]}


def test_a_person_with_only_a_few_faces_keeps_small_stages(session):
    """No stage reaches the minimum, so dropping small ones would leave no gallery at
    all -- and best-over-stages matching has nothing to take the max of."""
    person = Person(name="New", birthdate=date(1990, 1, 28))
    p1991, p2020 = MediaItem(year=1991), MediaItem(year=2020)
    session.add_all([person, p1991, p2020])
    session.flush()
    _add_face(session, person, p1991, _unit(1.0), age=1)
    _add_face(session, person, p2020, _unit(0.0, 1.0), age=30)
    session.commit()

    update_person_embedding(person.id, session)
    session.expire_all()

    assert set(_stage_faces(session, person)) == {"baby", "adult"}


def test_a_child_gets_no_stage_older_than_their_current_age(session):
    """A 10-year-old: undated photos whose predicted age is adult are filed under the
    child stage, so no adult medoid is ever built. (Birthdate relative to today, since
    the cap is the person's current age.)"""
    person = Person(name="Chase", birthdate=date(date.today().year - 10, 1, 28))
    undated = MediaItem(year=None)
    session.add_all([person, undated])
    session.flush()
    faces = [_add_face(session, person, undated, _unit(1.0, 0.01 * i), age=24) for i in range(3)]
    session.commit()

    update_person_embedding(person.id, session)
    session.expire_all()

    assert _stage_faces(session, person) == {"child": [face.id for face in faces]}


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


# --- bulk_link_faces_to_people ------------------------------------------------------

def _plain_face(sess, status: str) -> Face:
    media_item = MediaItem(full_file_path=f"/photos/{status}-{sess.query(MediaItem).count()}.jpg")
    sess.add(media_item)
    sess.flush()
    face = Face(media_item_id=media_item.id, status=status)
    sess.add(face)
    sess.commit()
    return face


def test_bulk_link_marks_linked_faces_assigned(session):
    person = Person(name="Ada")
    session.add(person)
    session.commit()
    faces = [_plain_face(session, FACE_STATUS_UNASSIGNED) for _ in range(3)]

    linked = bulk_link_faces_to_people(session, [(person.id, face.id) for face in faces])

    assert linked == 3
    for face in faces:
        session.refresh(face)
        assert face.status == FACE_STATUS_ASSIGNED
    assert {pf.face_id for pf in session.query(PersonFace)} == {face.id for face in faces}


def test_bulk_link_never_overrides_ignored_processing_or_linked_faces(session):
    ada, bob = Person(name="Ada"), Person(name="Bob")
    session.add_all([ada, bob])
    session.commit()
    ignored = _plain_face(session, FACE_STATUS_IGNORED)
    processing = _plain_face(session, FACE_STATUS_PROCESSING)
    already = _plain_face(session, FACE_STATUS_ASSIGNED)
    session.add(PersonFace(person_id=bob.id, face_id=already.id))
    session.commit()

    linked = bulk_link_faces_to_people(
        session, [(ada.id, ignored.id), (ada.id, processing.id), (ada.id, already.id)])

    assert linked == 0
    for face, status in ((ignored, FACE_STATUS_IGNORED), (processing, FACE_STATUS_PROCESSING)):
        session.refresh(face)
        assert face.status == status
    assert [(pf.person_id, pf.face_id) for pf in session.query(PersonFace)] == [(bob.id, already.id)]


def test_bulk_link_first_match_wins_for_a_repeated_face(session):
    ada, bob = Person(name="Ada"), Person(name="Bob")
    session.add_all([ada, bob])
    session.commit()
    face = _plain_face(session, FACE_STATUS_UNASSIGNED)

    assert bulk_link_faces_to_people(session, [(ada.id, face.id), (bob.id, face.id)]) == 1
    assert [(pf.person_id, pf.face_id) for pf in session.query(PersonFace)] == [(ada.id, face.id)]


# --- likely_misassigned_faces -------------------------------------------------------

def _person_with_faces(sess, name, birthdate, photo, vectors):
    person = Person(name=name, birthdate=birthdate)
    sess.add(person)
    sess.flush()
    faces = [_add_face(sess, person, photo, vec, age=5) for vec in vectors]
    sess.commit()
    update_person_embedding(person.id, sess)
    return person, faces


def test_flags_faces_that_look_more_like_someone_else_biggest_gap_first(session):
    photo = MediaItem(year=2022)
    session.add(photo)
    session.flush()
    nathan, _ = _person_with_faces(session, "Nathan", date(2017, 1, 1), photo,
                                   [_unit(0.0, 1.0, 0.01 * i) for i in range(3)])
    chase, chase_faces = _person_with_faces(
        session, "Chase", date(2016, 1, 1), photo,
        [_unit(1.0, 0.01 * i) for i in range(3)] + [_unit(0.3, 1.0), _unit(0.6, 1.0)])
    really_nathan, leaning_nathan = chase_faces[3], chase_faces[4]

    matches = likely_misassigned_faces(session, chase.id)

    assert [m.face_id for m in matches] == [really_nathan.id, leaning_nathan.id]  # biggest gap first
    assert {m.other_person_name for m in matches} == {"Nathan"}
    assert all(m.other_similarity > m.similarity for m in matches)
    assert likely_misassigned_faces(session, nathan.id) == []
    # Narrowed to the screen's filtered faces.
    assert [m.face_id for m in likely_misassigned_faces(session, chase.id, [leaning_nathan.id])] == [leaning_nathan.id]


def test_a_photo_from_before_someone_was_born_never_looks_like_them(session):
    old, new = MediaItem(year=2016), MediaItem(year=2022)
    session.add_all([old, new])
    session.flush()
    _person_with_faces(session, "Nathan", date(2019, 9, 6), new, [_unit(0.0, 1.0, 0.01 * i) for i in range(3)])
    chase = Person(name="Chase", birthdate=date(2016, 1, 28))
    session.add(chase)
    session.flush()
    for i in range(3):
        _add_face(session, chase, new, _unit(1.0, 0.01 * i), age=5)
    lookalike_2016 = _add_face(session, chase, old, _unit(0.0, 1.0), age=0)  # resembles Nathan, but 2016
    session.commit()
    update_person_embedding(chase.id, session)

    assert lookalike_2016.id not in {m.face_id for m in likely_misassigned_faces(session, chase.id)}


def _three_d(x, y):
    """A unit vector with cosine x to [1,0,...] and y to [0,1,...]: the rest of its
    length goes into a third axis neither gallery uses."""
    return _unit(x, y, float(np.sqrt(1 - x * x - y * y)))


def _two_people_and(session, extra_vectors):
    """Chase looks like [1,0], Nathan like [0,1]; `extra_vectors` are more of Chase's
    faces. (The plausible-match score is 0.35 for buffalo_l.)"""
    photo = MediaItem(year=2022)
    session.add(photo)
    session.flush()
    _person_with_faces(session, "Nathan", date(2017, 1, 1), photo, [_unit(0.01 * i, 1.0) for i in range(3)])
    chase, faces = _person_with_faces(session, "Chase", date(2016, 1, 1), photo,
                                      [_unit(1.0, 0.01 * i) for i in range(3)] + list(extra_vectors))
    return chase, faces[3:]


def test_a_face_that_matches_nobody_is_not_flagged_as_misassigned(session):
    """0.13 to Chase, 0.15 to Nathan: Nathan is 'higher' only on noise, below the
    band's floor -- the card used to read "0% vs 0%"."""
    chase, [stray] = _two_people_and(session, [_three_d(0.13, 0.15)])

    assert stray.id not in {m.face_id for m in likely_misassigned_faces(session, chase.id)}
    assert [m.face_id for m in unmatched_faces(session, chase.id)] == [stray.id]


def test_unmatched_faces_are_weakest_first_and_skip_faces_that_match_someone(session):
    chase, [weakest, weak, looks_like_nathan] = _two_people_and(
        session, [_three_d(0.05, 0.10), _three_d(0.20, 0.10), _three_d(0.10, 0.80)])

    unmatched = unmatched_faces(session, chase.id)

    assert [m.face_id for m in unmatched] == [weakest.id, weak.id]
    assert [m.face_id for m in likely_misassigned_faces(session, chase.id)] == [looks_like_nathan.id]
    # Narrowed to the screen's filtered faces, like the misassigned check.
    assert [m.face_id for m in unmatched_faces(session, chase.id, [weak.id])] == [weak.id]
