"""Home gallery filters. The file-path filter does a partial, case-insensitive
match against any part of the stored path (folders or file name) and repopulates
the input on the rendered page.

The gallery cards don't print the path (they link to /media/view/<id>), so
assertions check which photo ids the filtered page renders."""
import pytest

from yaffo.db import db
from yaffo.db.models import ApplicationSettings, Face, MediaItem, Person, PersonFace
from yaffo.distance_units import DISTANCE_UNIT_SETTING


@pytest.fixture
def media_item_ids(app):
    """Three photos with distinct paths; returns {label: id}."""
    with app.app_context():
        rows = {
            "img1234": MediaItem(full_file_path="/media/organized/2021/IMG_1234.jpg"),
            "img5678": MediaItem(full_file_path="/media/organized/2021/IMG_5678.jpg"),
            "beach": MediaItem(full_file_path="/media/organized/2020/vacation_beach.png"),
        }
        db.session.add_all(rows.values())
        db.session.commit()
        return {label: media_item.id for label, media_item in rows.items()}


def _rendered_ids(html: str) -> set[int]:
    import re
    return {int(m) for m in re.findall(r"/media/view/(\d+)", html)}


def test_path_filter_matches_file_name(client, media_item_ids):
    response = client.get("/?path=IMG_")

    assert response.status_code == 200
    assert _rendered_ids(response.data.decode()) == {media_item_ids["img1234"], media_item_ids["img5678"]}


def test_path_filter_matches_folder_segment(client, media_item_ids):
    response = client.get("/?path=2021")

    # matches a folder anywhere in the path, not just the file name
    assert _rendered_ids(response.data.decode()) == {media_item_ids["img1234"], media_item_ids["img5678"]}


def test_path_filter_is_case_insensitive(client, media_item_ids):
    response = client.get("/?path=img_1234")

    assert _rendered_ids(response.data.decode()) == {media_item_ids["img1234"]}


def test_path_filter_repopulates_input(client, media_item_ids):
    response = client.get("/?path=beach")

    body = response.data.decode()
    assert 'value="beach"' in body
    assert _rendered_ids(body) == {media_item_ids["beach"]}


def test_blank_path_filter_matches_all(client, media_item_ids):
    response = client.get("/?path=%20%20")  # whitespace only

    assert _rendered_ids(response.data.decode()) == set(media_item_ids.values())


def test_card_hover_details_split_name_and_folder(client, media_item_ids):
    body = client.get("/").data.decode()

    assert "photo-hover" in body
    assert "<dt>Name</dt>" in body
    assert "<dt>Folder</dt>" in body
    # the path is split into file name + parent folder
    assert ">IMG_1234.jpg</dd>" in body
    assert ">/media/organized/2021</dd>" in body
    assert ">vacation_beach.png</dd>" in body
    assert ">/media/organized/2020</dd>" in body


def test_favorite_filter_shows_only_favorites(client, app):
    with app.app_context():
        fav = MediaItem(full_file_path="/media/a.jpg", favorite=True)
        plain = MediaItem(full_file_path="/media/b.jpg")  # favorite NULL
        db.session.add_all([fav, plain])
        db.session.commit()
        fav_id, plain_id = fav.id, plain.id

    all_ids = _rendered_ids(client.get("/").data.decode())
    assert {fav_id, plain_id} <= all_ids

    only_favs = _rendered_ids(client.get("/?favorite=1").data.decode())
    assert fav_id in only_favs and plain_id not in only_favs


def test_proximity_filter_uses_saved_kilometers(client, app):
    with app.app_context():
        near = MediaItem(full_file_path="/media/near.jpg", latitude=38.6005, longitude=-90.2000)
        far = MediaItem(full_file_path="/media/far.jpg", latitude=38.6200, longitude=-90.2000)
        db.session.add_all([
            near,
            far,
            ApplicationSettings(name=DISTANCE_UNIT_SETTING, type="string", value="km"),
        ])
        db.session.commit()
        near_id, far_id = near.id, far.id

    body = client.get(
        "/?proximity-lat=38.6000&proximity-lon=-90.2000&proximity-distance=1"
    ).data.decode()

    assert "Within (Kilometers):" in body
    assert near_id in _rendered_ids(body)
    assert far_id not in _rendered_ids(body)


def test_gender_filter_prefers_person_gender_and_falls_back_to_face_estimate(client, app):
    with app.app_context():
        person_override_match = Person(name="Override match", gender=1)
        person_override_exclude = Person(name="Override exclude", gender=0)
        person_fallback = Person(name="Fallback", gender=None)
        db.session.add_all([person_override_match, person_override_exclude, person_fallback])
        db.session.flush()

        override_match = MediaItem(full_file_path="/media/override-match.jpg")
        override_exclude = MediaItem(full_file_path="/media/override-exclude.jpg")
        fallback = MediaItem(full_file_path="/media/fallback.jpg")
        unassigned = MediaItem(full_file_path="/media/unassigned.jpg")
        db.session.add_all([override_match, override_exclude, fallback, unassigned])
        db.session.flush()

        faces = [
            Face(full_file_path="/faces/override-match.jpg", media_item_id=override_match.id, gender=0),
            Face(full_file_path="/faces/override-exclude.jpg", media_item_id=override_exclude.id, gender=1),
            Face(full_file_path="/faces/fallback.jpg", media_item_id=fallback.id, gender=1),
            Face(full_file_path="/faces/unassigned.jpg", media_item_id=unassigned.id, gender=1),
        ]
        db.session.add_all(faces)
        db.session.flush()
        db.session.add_all([
            PersonFace(person_id=person_override_match.id, face_id=faces[0].id),
            PersonFace(person_id=person_override_exclude.id, face_id=faces[1].id),
            PersonFace(person_id=person_fallback.id, face_id=faces[2].id),
        ])
        db.session.commit()
        expected_ids = {override_match.id, fallback.id, unassigned.id}

    response = client.get("/?gender=1")

    assert response.status_code == 200
    assert _rendered_ids(response.data.decode()) == expected_ids


class TestConfigurableFilterLayout:
    """The 'Configure' modal + saved layout control which filters render and their
    order (scoped to this page via the home_filter_layout setting)."""

    def test_index_renders_configure_button_and_modal(self, client):
        body = client.get("/").data.decode()
        assert 'id="configure-filters-btn"' in body
        assert 'id="configureFiltersModal"' in body
        # every filter is listed in the modal (visible + hidden), even with no data
        assert 'data-key="year"' in body and 'data-key="device"' in body

    def test_all_filters_visible_by_default(self, client):
        body = client.get("/").data.decode()
        # year + device filter controls both present in the sidebar form
        assert 'id="year-select"' in body
        assert 'id="device-select"' in body

    def test_saving_hides_a_filter(self, client):
        resp = client.post("/settings/home-filters", json={"items": [
            {"key": "year", "visible": False},
            {"key": "device", "visible": True},
        ]})
        assert resp.status_code == 204

        body = client.get("/").data.decode()
        assert 'id="year-select"' not in body      # hidden -> control not rendered
        assert 'id="device-select"' in body        # still visible
        assert 'data-key="year"' in body           # but still listed in the modal

    def test_saving_reorders_filters(self, client):
        client.post("/settings/home-filters", json={"items": [
            {"key": "device", "visible": True},
            {"key": "year", "visible": True},
        ]})
        body = client.get("/").data.decode()
        # device now renders before year in the sidebar
        assert body.index('id="device-select"') < body.index('id="year-select"')

    def test_save_rejects_non_list_items(self, client):
        resp = client.post("/settings/home-filters", json={"items": "nope"})
        assert resp.status_code == 400


@pytest.fixture
def mixed_media_ids(app):
    """One photo + one video; returns {label: id}."""
    from yaffo.db.models import MEDIA_TYPE_VIDEO
    with app.app_context():
        rows = {
            "photo": MediaItem(full_file_path="/media/organized/2021/IMG_1.jpg"),
            "video": MediaItem(full_file_path="/media/organized/2021/CLIP.mp4",
                               media_type=MEDIA_TYPE_VIDEO),
        }
        db.session.add_all(rows.values())
        db.session.commit()
        return {label: m.id for label, m in rows.items()}


def test_media_type_filter_videos_only(client, mixed_media_ids):
    response = client.get("/?media-type=video")
    assert response.status_code == 200
    body = response.data.decode()
    assert _rendered_ids(body) == {mixed_media_ids["video"]}
    # The pager (page-size selector + nav links) must carry the active filter so
    # paging/resizing doesn't drop it.
    assert "media-type=video" in body


def test_media_type_filter_photos_only(client, mixed_media_ids):
    response = client.get("/?media-type=photo")
    assert _rendered_ids(response.data.decode()) == {mixed_media_ids["photo"]}


def test_media_type_filter_blank_shows_all(client, mixed_media_ids):
    response = client.get("/?media-type=")
    assert _rendered_ids(response.data.decode()) == set(mixed_media_ids.values())


def test_media_type_filter_ignores_garbage(client, mixed_media_ids):
    # An unknown value is treated as no filter, not an empty result.
    response = client.get("/?media-type=bogus")
    assert _rendered_ids(response.data.decode()) == set(mixed_media_ids.values())
