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
from yaffo.db.models import Face, MediaItem, FACE_STATUS_UNASSIGNED


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
