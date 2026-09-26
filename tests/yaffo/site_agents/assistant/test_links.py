"""The assistant's link tools, against a real library database."""
import pytest
from flask import url_for
from urllib.parse import parse_qs, urlsplit

from yaffo.db import db
from yaffo.db.models import Face, MediaItem, Person, PersonFace, FACE_STATUS_ASSIGNED
from yaffo.site_agents.assistant.links import LINK_TO_PAGE, LINK_TO_PHOTOS, LinkToolProvider, gallery_url

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


def test_links_match_flasks_own_urls(app, library):
    """The page table is built from the routes, so a link is what url_for gives."""
    with app.test_request_context():
        ours = urlsplit(gallery_url({"person_ids": [3], "year": 2019}))
        flasks = urlsplit(url_for("index", person=[3], year=2019))
        assert (ours.path, parse_qs(ours.query)) == (flasks.path, parse_qs(flasks.query))
        assert LinkToolProvider(db.session).call_tool(LINK_TO_PAGE, {
            "title": "x", "page": "person_faces", "values": {"person_id": library["chase"]},
        }).host_data["links"][0]["url"] == url_for("person_faces", person_id=library["chase"])


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


def test_page_link_to_one_photo(links, library):
    result = links.call_tool(LINK_TO_PAGE, {
        "title": "The canyon shot", "page": "media_view", "values": {"media_item_id": library["photo"]}})
    assert result.host_data["links"] == [{"title": "The canyon shot", "url": f"/media/view/{library['photo']}"}]


def test_page_link_without_parameters(links):
    result = links.call_tool(LINK_TO_PAGE, {"title": "Settings", "page": "settings_index"})
    assert result.host_data["links"] == [{"title": "Settings", "url": "/settings"}]


@pytest.mark.parametrize("args, message", [
    ({"page": "nope"}, "No page named 'nope'"),
    ({"page": "media_view", "values": {"media_item_id": 9999}}, "no media item with id 9999"),
    ({"page": "albums_show", "values": {"album_id": 1}}, "no album with id 1"),
    ({"page": "person_faces", "values": {}}, "person_id is required"),
    ({"page": "person_faces", "values": {"person_id": "ten"}}, "person_id must be a number"),
    ({"page": "automations_show", "values": {"slug": "a/b"}}, "slug can't contain '/'"),
    ({"page": "settings_index", "values": {"tab": "x"}}, "settings_index takes no tab"),
])
def test_page_link_problems_are_reported(links, library, args, message):
    result = links.call_tool(LINK_TO_PAGE, {"title": "x", **args})
    assert result.host_data["links"] == [] and result.host_data["error"] is True
    assert message in result.model_text


def test_page_catalog_lists_pages_with_their_parameters(links):
    [_, page_tool] = links.get_tools()
    assert "- person_faces(person_id): The faces assigned to one person" in page_tool.description
    assert "- settings_index: Settings:" in page_tool.description
    assert "media_view" in page_tool.input_schema["properties"]["page"]["enum"]


def test_gallery_url_without_filters():
    assert gallery_url({}) == "/"
