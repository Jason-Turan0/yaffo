"""The assistant's link tools, against a real library database."""
import pytest
from flask import url_for
from urllib.parse import parse_qs, urlsplit

from yaffo.db import db
from yaffo.db.models import Face, MediaItem, Person, PersonFace, FACE_STATUS_ASSIGNED
from yaffo.domain.media_filter_params import GALLERY_PATH, MEDIA_ITEM_PATH
from yaffo.site_agents.assistant.links import LINK_TO_MEDIA_ITEM, LINK_TO_PHOTOS, LinkToolProvider, gallery_url

pytestmark = pytest.mark.unit


@pytest.fixture
def library(app):
    photos = [MediaItem(full_file_path=f"/lib/{i}.jpg", year=2019 if i < 3 else 2020) for i in range(1, 5)]
    chase = Person(name="Chase")
    db.session.add_all([*photos, chase])
    db.session.flush()
    face = Face(full_file_path="/f/1.jpg", media_item_id=photos[0].id, status=FACE_STATUS_ASSIGNED)
    db.session.add(face)
    db.session.flush()
    db.session.add(PersonFace(person_id=chase.id, face_id=face.id))
    db.session.commit()
    return {"photo": photos[0].id, "chase": chase.id}


@pytest.fixture
def links(app):
    return LinkToolProvider(db.session)


def test_paths_match_the_app_routes(app):
    with app.test_request_context():
        assert url_for("index") == GALLERY_PATH
        assert url_for("media_view", media_item_id=546) == MEDIA_ITEM_PATH.format(media_item_id=546)


def test_gallery_link_uses_the_filter_panels_parameters(links, library):
    result = links.call_tool(LINK_TO_PHOTOS, {
        "title": "Chase in 2019", "filters": {"person_ids": [library["chase"]], "year": 2019}, "view": "grid"})

    [link] = result.host_data["links"]
    assert link["title"] == "Chase in 2019"
    url = urlsplit(link["url"])
    assert url.path == "/"
    assert parse_qs(url.query) == {"person": [str(library["chase"])], "year": ["2019"], "view": ["grid"]}
    assert result.host_data["count"] == 1 and result.host_data["error"] is False
    assert "1 item(s) match" in result.model_text and link["url"] not in result.model_text


def test_non_default_match_type_is_kept(links, library):
    result = links.call_tool(LINK_TO_PHOTOS, {
        "title": "Both", "filters": {"person_ids": [library["chase"]], "person_match_type": "all"}})
    assert "person-match-type=all" in result.host_data["links"][0]["url"]


def test_no_link_when_nothing_matches(links, library):
    result = links.call_tool(LINK_TO_PHOTOS, {"title": "1999", "filters": {"year": 1999}})
    assert result.host_data["links"] == [] and "No items match" in result.model_text


def test_unknown_people_and_invalid_filters_are_reported(links, library):
    result = links.call_tool(LINK_TO_PHOTOS, {
        "title": "x", "filters": {"person_ids": [999], "shape": "round"}})
    assert result.host_data["error"] is True
    assert "no people with ids [999]" in result.model_text and "shape" in result.model_text


def test_media_item_link(links, library):
    result = links.call_tool(LINK_TO_MEDIA_ITEM, {"title": "The canyon shot", "media_item_id": library["photo"]})
    assert result.host_data["links"] == [{"title": "The canyon shot", "url": f"/media/view/{library['photo']}"}]

    missing = links.call_tool(LINK_TO_MEDIA_ITEM, {"title": "x", "media_item_id": 9999})
    assert missing.host_data["links"] == [] and "No media item" in missing.model_text


def test_gallery_url_without_filters():
    assert gallery_url({}) == "/"
