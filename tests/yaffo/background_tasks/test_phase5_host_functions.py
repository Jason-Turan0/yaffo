"""Phase 5 host functions (ai-assistant.md → Additional actions): the reads run
live, the mutations restore what they changed on undo."""
import json

import numpy as np
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from yaffo.background_tasks.automation_sandbox import automation_actions as actions
from yaffo.background_tasks.automation_sandbox import automation_compare as compare
from yaffo import themes
from yaffo.background_tasks.automation_sandbox import duplicates, location_suggestions, preferences, undo
from yaffo.db import db
from yaffo.distance_units import get_saved_distance_unit
from yaffo.i18n import get_saved_locale
from yaffo.db.models import (
    FACE_STATUS_UNASSIGNED, JOB_STATUS_COMPLETED, JOB_STATUS_RUNNING, ClassificationLabel, Face, Job, JobResult,
    MediaItem, MediaLabel, Person, PersonEmbedding, Tag,
)

pytestmark = pytest.mark.unit


def _unit(vector) -> bytes:
    array = np.array(vector, dtype=np.float32)
    return (array / np.linalg.norm(array)).tobytes()


@pytest.fixture
def session(tmp_path, monkeypatch):
    monkeypatch.setattr(actions, "emit_event", lambda *args: None)
    monkeypatch.setattr(themes, "_cached_theme", None)  # set_theme caches the process-wide theme
    engine = create_engine(f"sqlite:///{tmp_path / 'lib.db'}")
    db.metadata.create_all(engine)
    with Session(engine) as session:
        session.add_all([MediaItem(id=i, full_file_path=f"/lib/{i}.jpg", year=2020,
                                   date_taken=f"2020-01-{i:02d} 10:00:00") for i in range(1, 13)])
        session.commit()
        yield session
    engine.dispose()


def _faces(session, vectors):
    """One unassigned face per photo, ids 1.. in order."""
    rng = np.random.default_rng(seed=3)
    for i, vector in enumerate(vectors, start=1):
        session.add(Face(id=i, media_item_id=i, status=FACE_STATUS_UNASSIGNED,
                         embedding=_unit(np.array(vector) + rng.normal(0, 0.003, 3))))
    session.commit()


# ---- faces and people --------------------------------------------------------------

def test_suggest_face_clusters_groups_look_alikes_largest_first(session):
    _faces(session, [[1, 0, 0]] * 4 + [[0, 1, 0]] * 3 + [[0, 0, 1]])

    clusters = compare.suggest_face_clusters(session, 80, 10)

    assert [c["size"] for c in clusters] == [4, 3]  # the lone face is noise
    assert clusters[0]["face_ids"] == [1, 2, 3, 4] and clusters[0]["media_item_ids"] == [1, 2, 3, 4]
    assert compare.suggest_face_clusters(session, 80, 1) == clusters[:1]
    with pytest.raises(ValueError):
        compare.suggest_face_clusters(session, 150)


def test_find_similar_faces_scores_unassigned_faces_against_a_person(session):
    _faces(session, [[1, 0, 0], [0.95, 0.05, 0], [0, 1, 0]])
    session.add(Person(id=1, name="Billy", avg_embedding=_unit([1, 0, 0])))
    session.add(PersonEmbedding(person_id=1, life_stage="unknown", avg_embedding=_unit([1, 0, 0])))
    session.commit()

    faces = compare.find_similar_faces(session, 1, 50, 10)

    assert [f["face_id"] for f in faces] == [1, 2]  # the other-looking face is below the threshold
    assert faces[0]["media_item_id"] == 1 and faces[0]["score"] >= faces[1]["score"]
    assert compare.find_similar_faces(session, 99) == []


def test_set_person_birthdate_and_undo_respects_a_later_change(session):
    session.add(Person(id=1, name="Billy"))
    session.commit()
    args = [1, "2015-06-01"]
    inverse = undo.set_person_birthdate(args, session)
    actions.set_person_birthdate(session, *args)
    assert actions.person_birthdate(session, 1) == "2015-06-01"

    actions.set_person_birthdate(session, *inverse[0].args)
    assert actions.person_birthdate(session, 1) is None

    actions.set_person_birthdate(session, *args)
    actions.set_person_birthdate(session, 1, "2016-01-01")  # changed since
    actions.set_person_birthdate(session, *inverse[0].args)
    assert actions.person_birthdate(session, 1) == "2016-01-01"
    with pytest.raises(ValueError):
        actions.set_person_birthdate(session, 1, "June 2015")


# ---- duplicates ---------------------------------------------------------------------

def test_duplicate_groups_names_copies_by_media_item_never_by_path(session):
    session.add_all([
        Job(id="scan", name="find_duplicates", status=JOB_STATUS_COMPLETED),
        Job(id="busy", name="find_duplicates", status=JOB_STATUS_RUNNING),
        JobResult(job_id="scan", task_id="t1", result_data=json.dumps([
            {"id": 0, "paths": ["/lib/1.jpg", "/lib/2.jpg", "/elsewhere/x.jpg"]},
            {"id": 1, "paths": ["/lib/3.jpg", "/lib/4.jpg"]},
        ])),
        Tag(media_item_id=2, tag_name="kept"),
        Face(id=50, media_item_id=2, status=FACE_STATUS_UNASSIGNED),
    ])
    session.commit()

    found = duplicates.duplicate_groups(session, "scan", 1)

    assert found["total_groups"] == 2 and len(found["groups"]) == 1
    group = found["groups"][0]
    assert [item["id"] for item in group["items"]] == [1, 2] and group["unindexed"] == 1
    assert group["items"][1]["tags"] == 1 and group["items"][1]["faces"] == 1
    assert "/lib" not in json.dumps(found)  # no full paths reach a script
    with pytest.raises(ValueError, match="hasn't finished"):
        duplicates.duplicate_groups(session, "busy")
    with pytest.raises(ValueError, match="No duplicate scan"):
        duplicates.duplicate_groups(session, "missing")


# ---- locations ----------------------------------------------------------------------

def test_set_coordinates_validates_and_undo_restores_each_item(session):
    item = session.get(MediaItem, 2)
    item.latitude, item.longitude = 10.0, 20.0
    session.commit()
    values = [{"id": 1, "latitude": 48.85, "longitude": 2.29}, {"id": 2, "latitude": 48.85, "longitude": 2.29}]
    inverse = undo.set_coordinates([values], session)
    actions.set_coordinates(session, values)
    actions.set_coordinates(session, [{"id": 2, "latitude": 1.0, "longitude": 1.0}])  # changed since

    actions.set_coordinates(session, *inverse[0].args)

    session.expire_all()
    assert (session.get(MediaItem, 1).latitude, session.get(MediaItem, 1).longitude) == (None, None)
    assert session.get(MediaItem, 2).latitude == 1.0
    for bad in ({"id": 1, "latitude": 91, "longitude": 0}, {"id": 1, "latitude": 10, "longitude": None}):
        with pytest.raises(ValueError):
            actions.set_coordinates(session, [bad])


def test_suggest_location_names_uses_the_closest_named_photo_within_the_radius(session):
    places = {1: (48.8584, 2.2945, "Eiffel Tower"), 2: (48.8606, 2.3376, "Louvre"),
              3: (48.8590, 2.2950, None), 4: (51.5, -0.12, None), 5: (None, None, None)}
    for item_id, (lat, lon, name) in places.items():
        item = session.get(MediaItem, item_id)
        item.latitude, item.longitude, item.location_name = lat, lon, name
    session.commit()

    suggestions = location_suggestions.suggest_location_names(session, [3, 4, 5, 1], 5)

    assert [s["id"] for s in suggestions] == [3, 4, 1]  # no GPS, no suggestion
    assert suggestions[0]["location_name"] == "Eiffel Tower" and suggestions[0]["distance_km"] < 0.2
    assert suggestions[1] == {"id": 4, "location_name": None, "distance_km": None}  # London: nothing named near
    assert suggestions[2]["location_name"] == "Louvre"  # not itself
    assert location_suggestions.default_radius_km(session) > 0
    with pytest.raises(ValueError):
        location_suggestions.suggest_location_names(session, [1], 0)


# ---- label vocabulary ---------------------------------------------------------------

def test_add_label_is_idempotent_and_undo_keeps_a_label_once_photos_have_it(session):
    existing = actions.add_label_to_vocabulary(session, "dog")
    assert undo.add_label_to_vocabulary(["dog"], session) == []  # it existed: nothing to undo
    assert actions.add_label_to_vocabulary(session, " dog ") == existing

    inverse = undo.resolve_undo_result(undo.add_label_to_vocabulary(["sailboat"], session),
                                       actions.add_label_to_vocabulary(session, "sailboat", "a sailboat"))
    label = session.query(ClassificationLabel).filter_by(name="sailboat").one()
    assert label.prompt == "a sailboat"
    session.add(MediaLabel(media_item_id=1, label_id=label.id, confidence=0.3))
    session.commit()
    actions.delete_label(session, *inverse[0].args)  # classified since: kept
    assert session.query(ClassificationLabel).filter_by(name="sailboat").count() == 1

    session.query(MediaLabel).delete()
    session.commit()
    actions.delete_label(session, *inverse[0].args)
    assert session.query(ClassificationLabel).filter_by(name="sailboat").count() == 0
    with pytest.raises(ValueError):
        actions.add_label_to_vocabulary(session, "   ")


# ---- preferences --------------------------------------------------------------------

def test_theme_language_and_unit_changes_are_undone_unless_changed_since(session):
    for set_value, undo_of, read, value, later in [
        (preferences.set_default_theme, preferences.undo_set_default_theme, themes.saved_theme, "darkroom", "memphis"),
        (preferences.set_locale, preferences.undo_set_locale, get_saved_locale, "de", "fr"),
        (preferences.set_distance_unit, preferences.undo_set_distance_unit, get_saved_distance_unit, "km", "mi"),
    ]:
        before = read(session)
        inverse = undo_of([value], session)
        set_value(session, value)
        assert read(session) == value
        set_value(session, *inverse[0].args)
        assert read(session) == before

        set_value(session, value)
        set_value(session, later)  # changed since
        set_value(session, *inverse[0].args)
        assert read(session) == later
    with pytest.raises(ValueError):
        preferences.set_locale(session, "xx")
    assert preferences.theme_exists(["nope"], session)


def test_following_the_browser_language_again_is_undone(session):
    preferences.set_locale(session, "es")
    inverse = preferences.undo_set_locale([None], session)
    preferences.set_locale(session, None)
    assert get_saved_locale(session) is None
    preferences.set_locale(session, *inverse[0].args)
    assert get_saved_locale(session) == "es"


def test_filter_layout_and_undo_restore_the_whole_order(session):
    before = preferences.filter_layout(session)
    items = [{"key": "people", "visible": True}, {"key": "year", "visible": False}]
    inverse = preferences.undo_set_filter_layout([items], session)

    preferences.set_filter_layout(session, items)

    after = preferences.filter_layout(session)
    assert after[:2] == items and len(after) == len(before)
    assert inverse[0].args[1] == {"value": after}  # the undo's expected matches what was saved
    preferences.set_filter_layout(session, *inverse[0].args)
    assert preferences.filter_layout(session) == before
