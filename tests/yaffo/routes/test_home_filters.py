"""Home gallery filters. The file-path filter does a partial, case-insensitive
match against any part of the stored path (folders or file name) and repopulates
the input on the rendered page.

The gallery cards don't print the path (they link to /photo/view/<id>), so
assertions check which photo ids the filtered page renders."""
import pytest

from yaffo.db import db
from yaffo.db.models import Photo


@pytest.fixture
def photo_ids(app):
    """Three photos with distinct paths; returns {label: id}."""
    with app.app_context():
        rows = {
            "img1234": Photo(full_file_path="/media/organized/2021/IMG_1234.jpg"),
            "img5678": Photo(full_file_path="/media/organized/2021/IMG_5678.jpg"),
            "beach": Photo(full_file_path="/media/organized/2020/vacation_beach.png"),
        }
        db.session.add_all(rows.values())
        db.session.commit()
        return {label: photo.id for label, photo in rows.items()}


def _rendered_ids(html: str) -> set[int]:
    import re
    return {int(m) for m in re.findall(r"/photo/view/(\d+)", html)}


def test_path_filter_matches_file_name(client, photo_ids):
    response = client.get("/?path=IMG_")

    assert response.status_code == 200
    assert _rendered_ids(response.data.decode()) == {photo_ids["img1234"], photo_ids["img5678"]}


def test_path_filter_matches_folder_segment(client, photo_ids):
    response = client.get("/?path=2021")

    # matches a folder anywhere in the path, not just the file name
    assert _rendered_ids(response.data.decode()) == {photo_ids["img1234"], photo_ids["img5678"]}


def test_path_filter_is_case_insensitive(client, photo_ids):
    response = client.get("/?path=img_1234")

    assert _rendered_ids(response.data.decode()) == {photo_ids["img1234"]}


def test_path_filter_repopulates_input(client, photo_ids):
    response = client.get("/?path=beach")

    body = response.data.decode()
    assert 'value="beach"' in body
    assert _rendered_ids(body) == {photo_ids["beach"]}


def test_blank_path_filter_matches_all(client, photo_ids):
    response = client.get("/?path=%20%20")  # whitespace only

    assert _rendered_ids(response.data.decode()) == set(photo_ids.values())


def test_card_hover_details_split_name_and_folder(client, photo_ids):
    body = client.get("/").data.decode()

    assert "photo-hover" in body
    assert "<dt>Name</dt>" in body
    assert "<dt>Folder</dt>" in body
    # the path is split into file name + parent folder
    assert ">IMG_1234.jpg</dd>" in body
    assert ">/media/organized/2021</dd>" in body
    assert ">vacation_beach.png</dd>" in body
    assert ">/media/organized/2020</dd>" in body
