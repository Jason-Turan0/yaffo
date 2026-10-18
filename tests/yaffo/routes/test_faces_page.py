"""End-to-end cover for the face-assignment page's cluster payload.

The page hands each cluster to the browser as a `data-faces` JSON blob, and the
hover tooltip is built entirely from it: the centroid similarity and the source
media the face was cropped from. Assert on that payload rather than on the
suggestion builders, so a field dropped anywhere between the model and the
template is caught.
"""
import json
import re

import numpy as np
import pytest

from yaffo.db import db
from yaffo.db.models import (
    ApplicationSettings, Face, MediaItem, Person, PersonEmbedding,
    FACE_STATUS_ASSIGNED, FACE_STATUS_IGNORED, FACE_STATUS_UNASSIGNED,
)


def _unit(vector) -> bytes:
    array = np.array(vector, dtype=np.float32)
    return (array / np.linalg.norm(array)).tobytes()


def _cluster_payloads(html: str) -> list[list[dict]]:
    return [json.loads(match) for match in re.findall(r"data-faces='([^']+)'", html)]


def test_faces_page_reports_centroid_similarity_and_source_media(app, client):
    # Four near-identical embeddings: tight enough for DBSCAN to call them one cluster.
    rng = np.random.default_rng(seed=7)
    for i in range(4):
        media_item = MediaItem(
            full_file_path=f"/photos/p{i}.jpg",
            media_type="photo",
            date_taken=f"2020-01-0{i + 1} 10:00:00",
        )
        db.session.add(media_item)
        db.session.flush()
        db.session.add(Face(
            media_item_id=media_item.id,
            embedding=_unit(np.array([1.0, 0.02, 0.0]) + rng.normal(0, 0.005, 3)),
            full_file_path=f"/thumbs/f{i}.jpg",
            status=FACE_STATUS_UNASSIGNED,
            location_top=10, location_right=90, location_bottom=80, location_left=20,
        ))
    db.session.commit()

    response = client.get("/faces?group_by=similarity")

    assert response.status_code == 200
    clusters = _cluster_payloads(response.get_data(as_text=True))
    assert len(clusters) == 1
    faces = clusters[0]
    assert len(faces) == 4
    for face in faces:
        # Similarity-grouped faces used to carry no score at all, which the UI
        # then painted as "0%".
        assert 0.0 < face["similarity"] <= 1.0
        assert face["media_item_id"] > 0
        assert face["media_type"] == "photo"
        assert face["region"] == {"top": 10, "right": 90, "bottom": 80, "left": 20}


def test_faces_page_orders_a_cluster_weakest_match_first(app, client):
    """Ascending similarity: the faces least like the rest of the cluster lead, so the
    ones most likely to be wrong are the ones you review before assigning."""
    rng = np.random.default_rng(seed=11)
    for i in range(5):
        media_item = MediaItem(full_file_path=f"/photos/o{i}.jpg", media_type="photo",
                               date_taken="2020-01-01 10:00:00")
        db.session.add(media_item)
        db.session.flush()
        # Spread the members out so their centroid similarities are distinct.
        jitter = rng.normal(0, 0.004 * (i + 1), 3)
        db.session.add(Face(
            media_item_id=media_item.id,
            embedding=_unit(np.array([1.0, 0.02, 0.0]) + jitter),
            full_file_path=f"/thumbs/o{i}.jpg",
            status=FACE_STATUS_UNASSIGNED,
        ))
    db.session.commit()

    response = client.get("/faces?group_by=similarity")

    faces = _cluster_payloads(response.get_data(as_text=True))[0]
    similarities = [face["similarity"] for face in faces]
    assert similarities == sorted(similarities)
    assert len(set(similarities)) > 1  # the fixture actually varies, so order means something


def test_faces_page_omits_the_region_when_the_box_was_never_recorded(app, client):
    media_item = MediaItem(full_file_path="/photos/p.jpg", media_type="photo",
                           date_taken="2020-01-01 10:00:00")
    db.session.add(media_item)
    db.session.flush()
    for i in range(3):
        db.session.add(Face(
            media_item_id=media_item.id,
            embedding=_unit([1.0, 0.02 + i / 1000, 0.0]),
            full_file_path=f"/thumbs/f{i}.jpg",
            status=FACE_STATUS_UNASSIGNED,
        ))
    db.session.commit()

    response = client.get("/faces?group_by=similarity")

    faces = _cluster_payloads(response.get_data(as_text=True))[0]
    assert [face["region"] for face in faces] == [None, None, None]


def test_faces_page_renders_editable_shortcut_people(app, client):
    ada = Person(name="Ada")
    bea = Person(name="Bea")
    cal = Person(name="Cal")
    db.session.add_all([ada, bea, cal])
    db.session.flush()
    db.session.add(ApplicationSettings(name="face_shortcut_people", type="json", value=json.dumps([cal.id, ada.id])))
    db.session.commit()

    response = client.get("/faces")
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert 'id="configure-shortcuts-btn"' in body
    assert 'data-icon="filter-config"' in body
    assert body.index(f'data-person-id="{cal.id}"') < body.index(f'data-person-id="{ada.id}"')


def test_face_shortcut_people_settings_save_and_reset(app, client):
    ada = Person(name="Ada")
    bea = Person(name="Bea")
    db.session.add_all([ada, bea])
    db.session.commit()

    save_response = client.post("/settings/faces/shortcuts", json={"person_ids": [bea.id, ada.id, bea.id]})

    assert save_response.status_code == 204
    setting = db.session.query(ApplicationSettings).filter_by(name="face_shortcut_people").one()
    assert json.loads(setting.value) == [bea.id, ada.id]

    reset_response = client.delete("/settings/faces/shortcuts")

    assert reset_response.status_code == 204
    assert db.session.query(ApplicationSettings).filter_by(name="face_shortcut_people").first() is None


def _add_cluster(statuses: list[str]) -> list[int]:
    """One tight cluster of faces (one per photo) with the given statuses, in order."""
    rng = np.random.default_rng(seed=5)
    ids = []
    for i, status in enumerate(statuses):
        media_item = MediaItem(full_file_path=f"/photos/i{i}.jpg", media_type="photo",
                               date_taken=f"2020-02-0{i + 1} 10:00:00")
        db.session.add(media_item)
        db.session.flush()
        face = Face(media_item_id=media_item.id, full_file_path=f"/thumbs/i{i}.jpg", status=status,
                    embedding=_unit(np.array([1.0, 0.02, 0.0]) + rng.normal(0, 0.005, 3)))
        db.session.add(face)
        db.session.flush()
        ids.append(face.id)
    db.session.commit()
    return ids


def test_ignored_faces_are_hidden_by_default(app, client):
    unassigned = _add_cluster([FACE_STATUS_UNASSIGNED] * 3 + [FACE_STATUS_IGNORED] * 2)[:3]

    html = client.get("/faces?group_by=similarity").get_data(as_text=True)

    faces = [face for cluster in _cluster_payloads(html) for face in cluster]
    assert sorted(face["id"] for face in faces) == unassigned
    assert not any(face["ignored"] for face in faces)
    assert 'name="include_ignored" value="1"' in html
    assert not re.search(r'name="include_ignored" value="1"\s+checked', html)
    assert "of 3 unassigned faces" in html


def test_include_ignored_filter_adds_and_marks_ignored_faces(app, client):
    ids = _add_cluster([FACE_STATUS_UNASSIGNED] * 3 + [FACE_STATUS_IGNORED] * 2)

    html = client.get("/faces?group_by=similarity&include_ignored=1").get_data(as_text=True)

    faces = [face for cluster in _cluster_payloads(html) for face in cluster]
    assert sorted(face["id"] for face in faces) == ids
    assert sorted(face["id"] for face in faces if face["ignored"]) == ids[3:]
    assert re.search(r'name="include_ignored" value="1"\s+checked', html)
    assert "of 5 unassigned and ignored faces" in html


def test_people_grouping_reports_each_persons_best_life_stage(app, client):
    """A face is scored against every life stage of a person; the page shows the best
    one. The stages were once ordered by name rather than score, so a weaker stage
    whose name sorts later ("teen" after "adult") supplied the reported similarity."""
    media_item = MediaItem(full_file_path="/photos/s.jpg", media_type="photo", date_taken="2020-03-01 10:00:00")
    db.session.add(media_item)
    db.session.flush()
    face_vector = np.array([1.0, 0.0, 0.0])
    adult_vector = np.array([1.0, 0.05, 0.0])  # close to the face
    teen_vector = np.array([1.0, 0.6, 0.0])    # still a match, but a weaker one
    db.session.add(Face(media_item_id=media_item.id, full_file_path="/thumbs/s.jpg",
                        status=FACE_STATUS_UNASSIGNED, embedding=_unit(face_vector)))
    person = Person(name="Chase")
    db.session.add(person)
    db.session.flush()
    db.session.add_all([
        PersonEmbedding(person_id=person.id, life_stage="adult", avg_embedding=_unit(adult_vector)),
        PersonEmbedding(person_id=person.id, life_stage="teen", avg_embedding=_unit(teen_vector)),
    ])
    db.session.commit()

    html = client.get("/faces?group_by=people&threshold=0").get_data(as_text=True)

    [faces] = _cluster_payloads(html)
    adult_similarity = float(face_vector @ adult_vector / np.linalg.norm(adult_vector))
    assert faces[0]["cosine_similarity"] == pytest.approx(adult_similarity, abs=1e-4)


def test_shown_similarity_is_on_the_threshold_sliders_scale(app, client, monkeypatch):
    """The tooltip shows `similarity` as a percent. It used to be the raw cosine, so a
    face filtered out at a 30% threshold could read "52%". It is now the fraction of
    the calibrated band, the same scale the slider filters on."""
    monkeypatch.setattr("yaffo.routes.faces.get_similarity_bounds", lambda session: (0.4453, 0.8297))
    media_item = MediaItem(full_file_path="/photos/u.jpg", media_type="photo", date_taken="2020-03-01 10:00:00")
    db.session.add(media_item)
    db.session.flush()
    person_vector = np.array([1.0, 0.0, 0.0])
    face_vector = np.array([0.5229, np.sqrt(1 - 0.5229 ** 2), 0.0])  # cosine 0.5229 to the person
    db.session.add(Face(media_item_id=media_item.id, full_file_path="/thumbs/u.jpg",
                        status=FACE_STATUS_UNASSIGNED, embedding=_unit(face_vector)))
    person = Person(name="Chase")
    db.session.add(person)
    db.session.flush()
    db.session.add(PersonEmbedding(person_id=person.id, life_stage="child", avg_embedding=_unit(person_vector)))
    db.session.commit()

    at_10 = _cluster_payloads(client.get("/faces?group_by=people&threshold=10").get_data(as_text=True))
    at_30 = _cluster_payloads(client.get("/faces?group_by=people&threshold=30").get_data(as_text=True))

    [[face]] = at_10
    assert face["cosine_similarity"] == pytest.approx(0.5229, abs=1e-4)
    assert face["similarity"] == pytest.approx((0.5229 - 0.4453) / (0.8297 - 0.4453), abs=1e-3)  # ~20%
    # At 30% it falls to the Unknown group, which shows no score -- consistent with ~20%.
    [[unknown]] = at_30
    assert unknown["similarity"] is None and unknown["cosine_similarity"] is None


def _add_person(name: str, vector) -> Person:
    person = Person(name=name)
    db.session.add(person)
    db.session.flush()
    db.session.add(PersonEmbedding(person_id=person.id, life_stage="unknown", avg_embedding=_unit(vector)))
    return person


def _add_unassigned_face(index: int, vector) -> int:
    media_item = MediaItem(full_file_path=f"/photos/m{index}.jpg", media_type="photo",
                           date_taken=f"2020-04-0{index + 1} 10:00:00")
    db.session.add(media_item)
    db.session.flush()
    face = Face(media_item_id=media_item.id, full_file_path=f"/thumbs/m{index}.jpg",
                status=FACE_STATUS_UNASSIGNED, embedding=_unit(vector))
    db.session.add(face)
    db.session.flush()
    return face.id


def test_people_grouping_can_narrow_to_several_people(app, client):
    ada = _add_person("Ada", [1.0, 0.0, 0.0])
    bea = _add_person("Bea", [0.0, 1.0, 0.0])
    _add_person("Cal", [0.0, 0.0, 1.0])
    for index, vector in enumerate([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]):
        _add_unassigned_face(index, vector)
    db.session.commit()

    html = client.get(f"/faces?group_by=people&threshold=0&person={ada.id}&person={bea.id}").get_data(as_text=True)

    names = re.findall(r'class="cluster-name">([^<]+)<', html)
    assert "Ada" in names and "Bea" in names
    assert "Cal" not in names  # not chosen: its face falls to Unknown
    # Both chosen people stay ticked in the multi-select, with no Any/All toggle.
    assert re.search(rf'name="person"\s+value="{ada.id}"[^>]*checked', html)
    assert re.search(rf'name="person"\s+value="{bea.id}"[^>]*checked', html)
    assert 'id="person-match-type"' not in html


def test_people_filter_is_hidden_and_disabled_when_grouping_by_similarity(app, client):
    ada = _add_person("Ada", [1.0, 0.0, 0.0])
    db.session.commit()

    similarity = client.get(f"/faces?group_by=similarity&person={ada.id}").get_data(as_text=True)
    people = client.get("/faces?group_by=people").get_data(as_text=True)

    assert re.search(r'id="people-filter-group"\s+hidden', similarity)
    assert re.search(rf'name="person"\s+value="{ada.id}"[^>]*disabled', similarity)
    assert 'id="people-filter-group">' in people  # shown, not hidden
    assert not re.search(r'name="person"[^>]*disabled', people)


# --- paging the pool with the `after` cursor ----------------------------------------

def _pool(dates: list) -> list[int]:
    """One unassigned face per date (None = undated photo); ids in insertion order."""
    ids = []
    for index, date_taken in enumerate(dates):
        media_item = MediaItem(full_file_path=f"/photos/pool{index}.jpg", media_type="photo", date_taken=date_taken)
        db.session.add(media_item)
        db.session.flush()
        face = Face(media_item_id=media_item.id, full_file_path=f"/thumbs/pool{index}.jpg",
                    status=FACE_STATUS_UNASSIGNED, embedding=_unit([1.0, 0.01 * index, 0.0]))
        db.session.add(face)
        db.session.flush()
        ids.append(face.id)
    db.session.commit()
    return ids


def _batch(client, query: str) -> tuple[list[int], str]:
    """The face ids in a People-grouped batch (no people: all land in Unknown) and the html."""
    html = client.get(f"/faces?group_by=people&threshold=0&{query}").get_data(as_text=True)
    return sorted(face["id"] for cluster in _cluster_payloads(html) for face in cluster), html


def _next_after(html: str):
    match = re.search(r'data-next-after="(\d+)"', html)
    return int(match.group(1)) if match else None


def test_batches_follow_photo_date_then_face_id(app, client):
    # Two faces share a date: the face id breaks the tie, so the order is stable.
    late, early, tie_b, tie_a = _pool(["2021-01-01 10:00:00", "2019-01-01 10:00:00",
                                       "2020-01-01 10:00:00", "2020-01-01 10:00:00"])

    first, html = _batch(client, "batch_size=2")
    assert first == sorted([early, tie_b])
    assert _next_after(html) == tie_b

    second, html = _batch(client, f"batch_size=2&after={tie_b}")
    assert second == sorted([tie_a, late])
    assert "Showing 3–4 of 4 unassigned faces" in html


def test_the_cursor_does_not_skip_faces_when_earlier_ones_are_assigned(app, client):
    """An offset would: assigning batch 1 shifts every later face forward."""
    ids = _pool([f"2020-01-0{day} 10:00:00" for day in range(1, 7)])
    first, html = _batch(client, "batch_size=2")
    assert first == ids[:2]
    for face_id in first:
        db.session.get(Face, face_id).status = FACE_STATUS_ASSIGNED
    db.session.commit()

    second, _html = _batch(client, f"batch_size=2&after={_next_after(html)}")

    assert second == ids[2:4]


def test_undated_photos_come_first_and_page_like_the_rest(app, client):
    dated, undated_a, undated_b = _pool(["2020-01-01 10:00:00", None, None])

    first, html = _batch(client, "batch_size=1")
    assert first == [undated_a]
    second, html = _batch(client, f"batch_size=1&after={_next_after(html)}")
    assert second == [undated_b]
    third, _html = _batch(client, f"batch_size=1&after={_next_after(html)}")
    assert third == [dated]


def test_paging_past_the_last_face_offers_to_start_over(app, client):
    ids = _pool(["2020-01-01 10:00:00", "2020-01-02 10:00:00"])

    faces, html = _batch(client, f"batch_size=2&after={ids[-1]}")

    assert faces == []
    assert 'id="faces-end-of-pool"' in html
    assert "All Faces Assigned!" not in html  # faces remain, just before the cursor
    assert 'id="faces-next-batch"' not in html
    start_over = re.search(r'id="faces-end-of-pool".*?href="([^"]+)"', html, re.S).group(1)
    assert "after=" not in start_over and "batch_size=2" in start_over


def test_paging_links_keep_the_other_filters(app, client):
    ids = _pool([f"2020-01-0{day} 10:00:00" for day in range(1, 5)])

    _faces, html = _batch(client, f"batch_size=2&include_ignored=1&after={ids[0]}")

    link = re.search(r'<a href="([^"]+)" class="action-link" id="faces-next-batch"', html).group(1).replace("&amp;", "&")
    assert f"after={ids[2]}" in link
    assert "include_ignored=1" in link and "batch_size=2" in link and "group_by=people" in link
    assert 'id="faces-start-over"' in html


def test_an_unknown_cursor_starts_from_the_beginning(app, client):
    ids = _pool(["2020-01-01 10:00:00", "2020-01-02 10:00:00"])

    faces, html = _batch(client, "batch_size=1&after=99999")

    assert faces == [ids[0]]
    assert 'id="faces-start-over"' not in html
