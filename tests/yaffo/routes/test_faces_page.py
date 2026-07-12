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

from yaffo.db import db
from yaffo.db.models import ApplicationSettings, Face, MediaItem, Person, FACE_STATUS_UNASSIGNED


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
