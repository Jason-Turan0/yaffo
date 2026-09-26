"""The assistant's link tools, against a real library database."""
import pytest
from flask import url_for
from urllib.parse import parse_qs, urlsplit

from yaffo.db import db
from yaffo.db.models import Face, MediaItem, Person, PersonFace, FACE_STATUS_ASSIGNED
from yaffo.site_agents.assistant.tool_providers.links import (
    LINK_TO_FILE, LINK_TO_PAGE, LINK_TO_PHOTOS, LinkToolProvider, gallery_url,
)

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
    page_tool = next(tool for tool in links.get_tools() if tool.name == LINK_TO_PAGE)
    assert "- person_faces(person_id): The faces assigned to one person" in page_tool.description
    assert "- settings_index: Settings:" in page_tool.description
    assert "media_view" in page_tool.input_schema["properties"]["page"]["enum"]


def test_gallery_url_without_filters():
    assert gallery_url({}) == "/"


@pytest.fixture
def media_folder(app, tmp_path, monkeypatch):
    """A configured media folder with one indexed photo in a subfolder."""
    from types import SimpleNamespace
    root = tmp_path / "Family Photos"
    (root / "2019").mkdir(parents=True)
    photo_path = root / "2019" / "a.jpg"
    photo_path.write_bytes(b"x")
    monkeypatch.setattr("yaffo.site_agents.assistant.file_targets.get_media_dir_entries",
                        lambda session: [SimpleNamespace(id="m1", path=root)])
    item = MediaItem(full_file_path=str(photo_path), year=2019)
    outside = MediaItem(full_file_path=str(tmp_path / "elsewhere.jpg"), year=2019)
    (tmp_path / "elsewhere.jpg").write_bytes(b"x")
    db.session.add_all([item, outside])
    db.session.commit()
    return {"root": root, "photo": item.id, "outside": outside.id}


def test_file_link_by_media_item_names_it_by_label_only(links, media_folder):
    result = links.call_tool(LINK_TO_FILE, {"title": "The photo", "media_item_id": media_folder["photo"], "show": "folder"})
    assert result.host_data["error"] is False
    assert result.host_data["opens"] == [{
        "title": "The photo", "show": "folder",
        "target": {"show": "folder", "media_item_id": media_folder["photo"], "media_dir_id": None, "path": ""},
    }]
    assert "[media folder m1]/2019/a.jpg" in result.model_text
    assert str(media_folder["root"]) not in result.model_text and "Family Photos" not in result.model_text


def test_file_link_by_media_folder_and_path(links, media_folder):
    result = links.call_tool(LINK_TO_FILE, {"title": "2019", "media_dir_id": "m1", "path": "2019", "show": "file"})
    assert result.host_data["opens"][0]["target"]["path"] == "2019"
    assert "folder [media folder m1]/2019" in result.model_text


@pytest.mark.parametrize("args,message", [
    ({"media_dir_id": "m1", "path": "../secrets"}, "can't go up"),
    ({"media_dir_id": "m1", "path": "/etc/passwd"}, "not an absolute path"),
    ({"media_dir_id": "m1", "path": "2020"}, "doesn't exist"),
    ({"media_dir_id": "nope", "path": ""}, "no media folder"),
    ({"media_item_id": 999999}, "no photo or video"),
    ({}, "either media_item_id"),
    ({"media_item_id": 1, "media_dir_id": "m1"}, "either media_item_id"),
])
def test_file_link_refuses_what_it_cannot_open(links, media_folder, args, message):
    result = links.call_tool(LINK_TO_FILE, {"title": "x", "show": "file", **args})
    assert result.host_data["error"] is True and result.host_data["opens"] == []
    assert message in result.model_text


def test_file_link_refuses_an_item_outside_the_media_folders(links, media_folder):
    result = links.call_tool(LINK_TO_FILE, {"title": "x", "show": "file", "media_item_id": media_folder["outside"]})
    assert result.host_data["error"] is True
    assert "isn't inside a configured media folder" in result.model_text
