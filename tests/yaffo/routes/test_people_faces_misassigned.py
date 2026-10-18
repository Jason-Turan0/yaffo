"""A person's faces page: the "Show" choice (all faces, likely misassigned -- faces
that look more like someone else -- or looks like nobody) and "Move Selected to"
(reassign them, rebuilding both people's galleries)."""
import json
import re
from datetime import date

import numpy as np
import pytest

from yaffo.db import db
from yaffo.db.models import FACE_STATUS_ASSIGNED, Face, MediaItem, Person, PersonEmbedding, PersonFace
from yaffo.db.repositories.person_repository import update_person_embedding
from yaffo.domain.compare_utils import serialize_embedding

pytestmark = pytest.mark.unit


def _unit(*values) -> np.ndarray:
    vector = np.zeros(512, dtype=np.float32)
    vector[:len(values)] = values
    return vector / np.linalg.norm(vector)


def _seed():
    """Nathan looks like [0, 1]; Chase like [1, 0]. Chase's last two faces really look
    like Nathan. Each face is on its own day's photo: galleries keep one representative
    face per capture day."""
    chase = Person(name="Chase", birthdate=date(2016, 1, 28))
    nathan = Person(name="Nathan", birthdate=date(2019, 9, 6))
    db.session.add_all([chase, nathan])
    db.session.flush()
    vectors = {
        chase.id: [_unit(1, 0.01 * i) for i in range(3)] + [_unit(0.2, 1), _unit(0.5, 1)],
        nathan.id: [_unit(0.01 * i, 1) for i in range(3)],
    }
    face_ids = {}
    for person_id, person_vectors in vectors.items():
        face_ids[person_id] = []
        for index, vector in enumerate(person_vectors):
            photo = MediaItem(full_file_path=f"/lib/{person_id}-{index}.jpg", year=2022,
                              date_taken=f"2022-06-{person_id * 10 + index:02d} 10:00:00")
            db.session.add(photo)
            db.session.flush()
            face = Face(media_item_id=photo.id, status=FACE_STATUS_ASSIGNED, embedding=serialize_embedding(vector),
                        full_file_path=f"/thumbs/{person_id}-{index}.jpg", estimated_age=5)
            db.session.add(face)
            db.session.flush()
            db.session.add(PersonFace(person_id=person_id, face_id=face.id))
            face_ids[person_id].append(face.id)
    db.session.commit()
    for person_id in vectors:
        update_person_embedding(person_id, db.session)
    return chase, nathan, face_ids


def _card_ids(html: str) -> list[int]:
    return [int(face_id) for face_id in re.findall(r'class="face-card"\s+data-face-id="(\d+)"', html)]


def test_misassigned_filter_shows_only_faces_closer_to_someone_else(app, client):
    chase, nathan, face_ids = _seed()
    lookalikes = face_ids[chase.id][3:]

    html = client.get(f"/people/{chase.id}/faces?show=misassigned").get_data(as_text=True)

    assert _card_ids(html) == lookalikes  # biggest gap first: [0.2, 1] before [0.5, 1]
    assert html.count("Closer to Nathan:") == 2
    assert re.search(r'name="show" value="misassigned"\s+checked', html)
    # The person most of them look like is preselected as the move target.
    assert re.search(rf'<option value="{nathan.id}" selected>Nathan</option>', html)


def test_without_the_filter_every_face_shows_and_none_is_flagged(app, client):
    chase, _nathan, face_ids = _seed()

    html = client.get(f"/people/{chase.id}/faces").get_data(as_text=True)

    assert sorted(_card_ids(html)) == face_ids[chase.id]
    assert "Closer to" not in html
    assert re.search(r'name="show" value="all"\s+checked', html)


def test_misassigned_filter_says_so_when_nothing_is_flagged(app, client):
    _chase, nathan, _face_ids = _seed()

    html = client.get(f"/people/{nathan.id}/faces?show=misassigned").get_data(as_text=True)

    assert _card_ids(html) == []
    assert "None of Nathan's faces look more like someone else." in html


def test_move_reassigns_the_faces_and_rebuilds_both_galleries(app, client):
    chase, nathan, face_ids = _seed()
    lookalikes = face_ids[chase.id][3:]

    response = client.post(f"/people/{chase.id}/faces/move",
                           data={"faces": [str(face_id) for face_id in lookalikes], "target_person": nathan.id})

    assert response.status_code == 302
    db.session.expire_all()
    owners = dict(db.session.query(PersonFace.face_id, PersonFace.person_id))
    assert [owners[face_id] for face_id in lookalikes] == [nathan.id, nathan.id]
    assert [owners[face_id] for face_id in face_ids[chase.id][:3]] == [chase.id] * 3
    # Both galleries were rebuilt from their new faces: the moved ones are Nathan's now.
    included = {(e.person_id, face_id) for e in db.session.query(PersonEmbedding)
                for face_id in json.loads(e.included_face_ids)}
    assert all((chase.id, face_id) not in included for face_id in lookalikes)
    assert all((nathan.id, face_id) in included for face_id in lookalikes)
    # And Chase no longer has faces that look like someone else.
    html = client.get(f"/people/{chase.id}/faces?show=misassigned").get_data(as_text=True)
    assert _card_ids(html) == []


def test_move_ignores_faces_that_are_not_this_persons(app, client):
    chase, nathan, face_ids = _seed()
    nathans_own = face_ids[nathan.id][0]

    client.post(f"/people/{chase.id}/faces/move",
                data={"faces": [str(nathans_own), str(face_ids[chase.id][3])], "target_person": nathan.id})

    db.session.expire_all()
    owners = dict(db.session.query(PersonFace.face_id, PersonFace.person_id))
    assert owners[nathans_own] == nathan.id
    assert owners[face_ids[chase.id][3]] == nathan.id


@pytest.mark.parametrize("target", ["", "999", "self"])
def test_move_needs_another_existing_person(app, client, target):
    chase, _nathan, face_ids = _seed()
    target_id = str(chase.id) if target == "self" else target

    client.post(f"/people/{chase.id}/faces/move",
                data={"faces": [str(face_ids[chase.id][3])], "target_person": target_id})

    db.session.expire_all()
    assert db.session.query(PersonFace.person_id).filter_by(face_id=face_ids[chase.id][3]).scalar() == chase.id


def test_looks_like_nobody_lists_faces_that_match_no_one(app, client):
    chase, _nathan, face_ids = _seed()
    stray = Face(media_item_id=db.session.get(Face, face_ids[chase.id][0]).media_item_id, status=FACE_STATUS_ASSIGNED,
                 embedding=serialize_embedding(_unit(0.13, 0.15, float(np.sqrt(1 - 0.13 ** 2 - 0.15 ** 2)))),
                 full_file_path="/thumbs/stray.jpg", estimated_age=5)
    db.session.add(stray)
    db.session.flush()
    db.session.add(PersonFace(person_id=chase.id, face_id=stray.id))
    db.session.commit()

    nobody = client.get(f"/people/{chase.id}/faces?show=nobody").get_data(as_text=True)
    misassigned = client.get(f"/people/{chase.id}/faces?show=misassigned").get_data(as_text=True)

    assert _card_ids(nobody) == [stray.id]
    assert "Closer to" not in nobody
    assert re.search(r'name="show" value="nobody"\s+checked', nobody)
    assert stray.id not in _card_ids(misassigned)


def test_looks_like_nobody_says_so_when_every_face_matches(app, client):
    _chase, nathan, _face_ids = _seed()

    html = client.get(f"/people/{nathan.id}/faces?show=nobody").get_data(as_text=True)

    assert _card_ids(html) == []
    assert "All of Nathan's faces clearly match someone." in html


def test_an_unknown_show_value_means_all_faces(app, client):
    chase, _nathan, face_ids = _seed()

    html = client.get(f"/people/{chase.id}/faces?show=bogus").get_data(as_text=True)

    assert sorted(_card_ids(html)) == face_ids[chase.id]
    assert re.search(r'name="show" value="all"\s+checked', html)


def test_card_percentages_show_one_decimal(app, client):
    chase, _nathan, _face_ids = _seed()

    html = client.get(f"/people/{chase.id}/faces?show=misassigned").get_data(as_text=True)

    similarity = re.findall(r'class="similarity-(?:high|medium|low)">\s*([^<]+?)\s*</span>', html)
    closer_to = re.findall(r'Closer to Nathan:\s*([^<]+?)\s*</div>', html)
    assert similarity and all(re.fullmatch(r"\d+\.\d%", value) for value in similarity), similarity
    assert closer_to and all(re.fullmatch(r"\d+\.\d%", value) for value in closer_to), closer_to
