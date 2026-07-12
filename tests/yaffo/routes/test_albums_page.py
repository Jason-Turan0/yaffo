"""Route tests for the Albums tab: the overview tiles, the album page, and the
create / edit-details / delete flows. Membership editing and sharing arrive with
their own screens; here the album page only lists what is in the album."""
import re
from datetime import datetime

import pytest

from yaffo.db import db
from yaffo.db.models import (
    GRANT_SCOPE_ALBUM,
    Album,
    MediaItem,
    TRUST_STATE_TRUSTED,
    KnownDevice,
)
from yaffo.db.repositories import album_repository as repo

pytestmark = pytest.mark.unit


def _photos(app, count=3):
    with app.app_context():
        for index in range(1, count + 1):
            db.session.add(
                MediaItem(
                    id=index,
                    full_file_path=f"/lib/{index}.jpg",
                    date_taken=datetime(2024, 7, index),
                    year=2024,
                    month=7,
                )
            )
        db.session.commit()


def _album(app, name="Summer 2024", items=(), description=None):
    with app.app_context():
        album = repo.create_album(db.session, name, description)
        if items:
            repo.add_items(db.session, album.id, list(items))
        return album.id


def _share(app, album_id, device_name="laptop"):
    """An active album grant — what makes an album show as Shared."""
    with app.app_context():
        db.session.add(
            KnownDevice(
                device_id="PEER-1",
                pubkey="k",
                display_name=device_name,
                trust_state=TRUST_STATE_TRUSTED,
            )
        )
        db.session.commit()
        from yaffo.db.repositories import p2p_repository

        p2p_repository.create_grant(db.session, "PEER-1", GRANT_SCOPE_ALBUM, album_id=album_id)


def test_overview_lists_albums_as_tiles(app, client):
    _photos(app)
    _album(app, "Summer 2024", items=[1, 2])
    _album(app, "Winter")

    body = client.get("/albums").get_data(as_text=True)

    assert "Summer 2024" in body and "Winter" in body
    assert "2 photos" in body and "0 photos" in body
    assert "/media/1" in body  # the first member stands in as the cover


def test_overview_uses_the_pinned_cover_when_set(app, client):
    _photos(app)
    album_id = _album(app, "Summer 2024", items=[1, 2])
    with app.app_context():
        repo.set_cover(db.session, album_id, 2)

    body = client.get("/albums").get_data(as_text=True)

    assert "/media/2" in body


def test_overview_draws_a_video_cover_from_its_poster(app, client):
    """/media/<id> serves the ORIGINAL file, so a video cover there streams an MP4
    into an <img> and fails (the fallback then shows "image not found"). Videos are
    covered by their poster, as on the home grid."""
    with app.app_context():
        db.session.add(
            MediaItem(
                id=1,
                full_file_path="/lib/clip.mp4",
                media_type="video",
                poster_path="/lib/clip.jpg",
                date_taken=datetime(2024, 7, 1),
                year=2024,
                month=7,
            )
        )
        db.session.commit()
    _album(app, "Movies", items=[1])

    body = client.get("/albums").get_data(as_text=True)

    assert "/media/1/poster" in body
    assert 'src="/media/1"' not in body  # would render the video file into an <img>


def test_overview_video_cover_without_a_poster_uses_the_video_placeholder(app, client):
    with app.app_context():
        db.session.add(
            MediaItem(id=1, full_file_path="/lib/clip.mp4", media_type="video",
                      date_taken=datetime(2024, 7, 1), year=2024, month=7)
        )
        db.session.commit()
    _album(app, "Movies", items=[1])

    body = client.get("/albums").get_data(as_text=True)

    assert "video_placeholder.svg" in body
    assert 'src="/media/1"' not in body


def test_overview_empty_state(app, client):
    body = client.get("/albums").get_data(as_text=True)
    assert "No albums yet" in body


def test_shared_album_is_chipped_in_the_sidebar_and_tiles(app, client):
    _photos(app)
    album_id = _album(app, "Summer 2024", items=[1])
    _share(app, album_id, device_name="laptop")

    body = client.get("/albums").get_data(as_text=True)
    assert '<span class="chip chip-accent">Shared</span>' in body

    body = client.get(f"/albums/{album_id}").get_data(as_text=True)
    assert "laptop" in body  # the header names the device it is shared with


def test_album_page_lists_members_in_manual_order(app, client):
    _photos(app)
    album_id = _album(app, "Trip", items=[3, 1])

    body = client.get(f"/albums/{album_id}").get_data(as_text=True)

    assert "Trip" in body
    assert "2 photos" in body
    assert body.index("/media/3") < body.index("/media/1")


def test_album_page_empty_state(app, client):
    album_id = _album(app, "Trip")
    body = client.get(f"/albums/{album_id}").get_data(as_text=True)
    assert "This album is empty" in body


def test_album_screens_load_the_photo_grid_styles(app, client):
    """The album grid and the add screen reuse .photo-grid/.photo-card, whose
    styles live in index.css — a per-page stylesheet that is easy to forget. Without
    it the cards render as full-width images instead of thumbnails."""
    _photos(app)
    album_id = _album(app, "Trip", items=[1])

    for url in (f"/albums/{album_id}", f"/albums/{album_id}/add"):
        body = client.get(url).get_data(as_text=True)
        assert "photo-grid" in body
        assert "index.css" in body, f"{url} renders a photo grid without its styles"


def test_album_page_unknown_404(app, client):
    assert client.get("/albums/999").status_code == 404


def test_create_album_redirects_to_it(app, client):
    resp = client.post("/albums/create", data={"name": "Summer 2024", "description": "beach"})

    assert resp.status_code == 302
    with app.app_context():
        album = repo.get_album_by_name(db.session, "Summer 2024")
        assert album is not None and album.description == "beach"
        assert resp.headers["Location"].endswith(f"/albums/{album.id}")


def test_create_album_rejects_a_duplicate_name(app, client):
    _album(app, "Summer 2024")

    resp = client.post("/albums/create", data={"name": "Summer 2024"})

    assert resp.status_code == 400
    assert "already exists" in resp.get_data(as_text=True)
    with app.app_context():
        assert len(repo.list_albums(db.session)) == 1


def test_create_album_requires_a_name(app, client):
    resp = client.post("/albums/create", data={"name": "  "})
    assert resp.status_code == 400
    assert "needs a name" in resp.get_data(as_text=True)


def test_edit_details_renames_the_album(app, client):
    album_id = _album(app, "Summer")

    resp = client.post(
        f"/albums/{album_id}/details", data={"name": "Summer 2024", "description": "beach"}
    )

    assert resp.status_code == 302
    with app.app_context():
        album = repo.get_album(db.session, album_id)
        assert album.name == "Summer 2024" and album.description == "beach"


def test_edit_details_rejects_a_clashing_name(app, client):
    _album(app, "Winter")
    album_id = _album(app, "Summer")

    resp = client.post(f"/albums/{album_id}/details", data={"name": "Winter"})

    assert resp.status_code == 400
    with app.app_context():
        assert repo.get_album(db.session, album_id).name == "Summer"


def test_add_screen_shows_the_filtered_library_and_the_match_scope(app, client):
    _photos(app, count=3)
    album_id = _album(app, "Trip")

    body = client.get(f"/albums/{album_id}/add").get_data(as_text=True)

    assert "Add photos to Trip" in body
    assert 'data-select-id="1"' in body
    assert "Select all 3 matching" in body  # the scope, not the page
    assert "0 in this album" in body  # the album's running count
    assert "Done" in body


def test_add_screen_scope_count_follows_the_filters(app, client):
    """The "select all N" count is the size of the FILTER MATCH, so it must move
    when the filters do — it is what scope=all will actually add."""
    _photos(app, count=3)
    with app.app_context():
        db.session.add(
            MediaItem(id=9, full_file_path="/lib/9.jpg", date_taken=datetime(2023, 1, 1),
                      year=2023, month=1)
        )
        db.session.commit()
    album_id = _album(app, "Trip")

    body = client.get(f"/albums/{album_id}/add?year=2024").get_data(as_text=True)

    assert "Select all 3 matching" in body
    assert 'data-select-id="9"' not in body  # the 2023 photo is filtered out


def test_add_selected_items_returns_to_the_add_screen(app, client):
    """Curating is a loop — filter, add, filter again — so adding stays on the add
    screen (carrying the filters) instead of bouncing to the album."""
    _photos(app)
    album_id = _album(app, "Trip")

    resp = client.post(f"/albums/{album_id}/items/add?year=2024&select_id=2&select_id=3")

    assert resp.status_code == 302
    location = resp.headers["Location"]
    assert f"/albums/{album_id}/add" in location
    assert "year=2024" in location  # the filters survive the POST
    assert "added=2" in location    # ...and the screen can confirm what landed
    with app.app_context():
        assert [item.id for item in repo.list_items(db.session, album_id)] == [2, 3]


def test_adding_twice_in_a_row(app, client):
    """The add screen's form copies the current querystring into its action, so a
    second add posts with the FIRST add's `added=N` still on it. That must not reach
    url_for as a second value for `added` (TypeError), and the confirmation must
    report this add rather than the previous one."""
    _photos(app, count=3)
    album_id = _album(app, "Trip")

    first = client.post(f"/albums/{album_id}/items/add?year=2024&select_id=1")
    assert "added=1" in first.headers["Location"]

    # POST again with the querystring the screen now carries — added=1 included.
    second = client.post(
        f"/albums/{album_id}/items/add?year=2024&added=1&select_id=2&select_id=3"
    )

    assert second.status_code == 302
    location = second.headers["Location"]
    assert location.count("added=") == 1  # not carried over as well as re-appended
    assert "added=2" in location          # this add, not the last one
    assert "year=2024" in location        # the real filters still survive
    with app.app_context():
        assert len(repo.list_items(db.session, album_id)) == 3


def test_add_screen_hides_photos_already_in_the_album(app, client):
    """Members are not offered: adding them is a no-op, and showing them would make
    the "N matching" count a lie about what Add would do."""
    _photos(app, count=3)
    album_id = _album(app, "Trip", items=[2])

    body = client.get(f"/albums/{album_id}/add").get_data(as_text=True)

    assert 'data-select-id="2"' not in body   # already a member
    assert 'data-select-id="1"' in body
    assert "Select all 2 matching" in body    # the count excludes the member
    assert "1 in this album" in body


def test_add_screen_says_when_everything_matching_is_already_in(app, client):
    _photos(app, count=3)
    album_id = _album(app, "Trip", items=[1, 2, 3])

    body = client.get(f"/albums/{album_id}/add").get_data(as_text=True)

    assert "Nothing left to add" in body
    assert "3 in this album" in body


def test_add_screen_confirms_what_was_added(app, client):
    _photos(app)
    album_id = _album(app, "Trip", items=[1])

    body = client.get(f"/albums/{album_id}/add?added=2").get_data(as_text=True)

    assert "2 photos added." in body


def test_add_scope_all_adds_every_photo_matching_the_filters(app, client):
    """The whole point of bulk add: the filters come on the querystring and the
    server re-runs them, so nothing enumerates ids."""
    _photos(app, count=3)
    with app.app_context():
        db.session.add(
            MediaItem(id=9, full_file_path="/lib/9.jpg", date_taken=datetime(2023, 1, 1),
                      year=2023, month=1)
        )
        db.session.commit()
    album_id = _album(app, "Trip")

    resp = client.post(f"/albums/{album_id}/items/add?year=2024&select=all")

    assert resp.status_code == 302
    assert "added=3" in resp.headers["Location"]
    with app.app_context():
        added = {item.id for item in repo.list_items(db.session, album_id)}
        assert added == {1, 2, 3}  # the 2023 photo was not matching, so not added


def test_add_skips_photos_already_in_the_album(app, client):
    _photos(app, count=3)
    album_id = _album(app, "Trip", items=[2])

    client.post(f"/albums/{album_id}/items/add?select=all")

    with app.app_context():
        items = [item.id for item in repo.list_items(db.session, album_id)]
        assert sorted(items) == [1, 2, 3]  # 2 is not duplicated
        assert len(items) == 3


def test_add_screen_unknown_album_404(app, client):
    assert client.get("/albums/999/add").status_code == 404


def test_add_scope_all_honors_exclusions(app, client):
    """"Select all, then untick a few" must survive: the selection is the scope MINUS
    the exclusions, and the exclusions travel with it. Unticking a photo on page 1
    and paging on must not silently re-add it."""
    _photos(app, count=3)
    album_id = _album(app, "Trip")

    resp = client.post(f"/albums/{album_id}/items/add?year=2024&select=all&exclude_id=2")

    assert resp.status_code == 302
    assert "added=2" in resp.headers["Location"]
    with app.app_context():
        assert {item.id for item in repo.list_items(db.session, album_id)} == {1, 3}


def test_remove_scope_all_honors_exclusions(app, client):
    _photos(app, count=3)
    album_id = _album(app, "Trip", items=[1, 2, 3])

    resp = client.post(f"/albums/{album_id}/items/remove?select=all&exclude_id=2")

    assert resp.status_code == 302
    with app.app_context():
        assert [item.id for item in repo.list_items(db.session, album_id)] == [2]
        assert db.session.query(MediaItem).count() == 3


def test_remove_selected_items_keeps_the_photos(app, client):
    _photos(app)
    album_id = _album(app, "Trip", items=[1, 2, 3])

    resp = client.post(f"/albums/{album_id}/items/remove?select_id=1&select_id=3")

    assert resp.status_code == 302
    with app.app_context():
        assert [item.id for item in repo.list_items(db.session, album_id)] == [2]
        assert db.session.query(MediaItem).count() == 3  # the photos survive


def test_remove_scope_all_empties_the_album(app, client):
    """"Select all" posts the scope, not the ids that happened to be rendered."""
    _photos(app)
    album_id = _album(app, "Trip", items=[1, 2, 3])

    resp = client.post(f"/albums/{album_id}/items/remove?select=all")

    assert resp.status_code == 302
    with app.app_context():
        assert repo.list_items(db.session, album_id) == []
        assert db.session.query(MediaItem).count() == 3


def test_set_cover(app, client):
    _photos(app)
    album_id = _album(app, "Trip", items=[1, 2])

    resp = client.post(f"/albums/{album_id}/cover", data={"media_item_id": "2"})

    assert resp.status_code == 302
    with app.app_context():
        assert repo.get_album(db.session, album_id).cover_media_item_id == 2


def test_set_cover_rejects_a_non_member(app, client):
    _photos(app)
    album_id = _album(app, "Trip", items=[1])

    resp = client.post(f"/albums/{album_id}/cover", data={"media_item_id": "2"})

    assert resp.status_code == 400
    assert "must be a member" in resp.get_data(as_text=True)
    with app.app_context():
        assert repo.get_album(db.session, album_id).cover_media_item_id is None


def test_reorder_persists_the_dragged_order(app, client):
    _photos(app)
    album_id = _album(app, "Trip", items=[1, 2, 3])

    resp = client.post(
        f"/albums/{album_id}/reorder", data={"media_item_id": ["3", "1", "2"]}
    )

    assert resp.status_code == 204  # the grid already moved the card
    with app.app_context():
        assert [item.id for item in repo.list_items(db.session, album_id)] == [3, 1, 2]


def test_album_page_marks_the_cover_and_offers_edit(app, client):
    _photos(app)
    album_id = _album(app, "Trip", items=[1, 2])
    with app.app_context():
        repo.set_cover(db.session, album_id, 2)

    body = client.get(f"/albums/{album_id}").get_data(as_text=True)

    assert f"/albums/{album_id}?edit=1" in body  # Edit is a link: the mode is in the URL
    assert "Cover" in body
    assert "Select all 2 photos" not in body  # the bar only appears in edit mode


def test_edit_mode_renders_the_selection_bar(app, client):
    _photos(app)
    album_id = _album(app, "Trip", items=[1, 2])

    body = client.get(f"/albums/{album_id}?edit=1").get_data(as_text=True)

    assert "is-selecting" in body
    assert 'data-select-id="1"' in body
    assert "Select all 2 photos" in body  # the scope, spelled out


def _ticked_ids(body: str) -> set[str]:
    """The ids of the cards the SERVER rendered as selected."""
    return {
        match.group(1)
        for match in re.finditer(
            r'class="[^"]*\bis-selected\b[^"]*"\s+data-select-id="(\d+)"', body
        )
    }


def test_selection_is_rendered_from_the_url(app, client):
    """The URL is the state: the server renders which cards are ticked, so the
    selection survives pagination, reload and the Back button without any
    client-side store."""
    _photos(app, count=3)
    album_id = _album(app, "Trip", items=[1, 2, 3])

    body = client.get(f"/albums/{album_id}?edit=1&select_id=2").get_data(as_text=True)
    assert _ticked_ids(body) == {"2"}
    assert "1 selected" in body

    # scope minus exclusions: everything except 2
    body = client.get(f"/albums/{album_id}?edit=1&select=all&exclude_id=2").get_data(as_text=True)
    assert _ticked_ids(body) == {"1", "3"}
    assert "2 selected" in body  # 3 members less the one excluded
    assert "Clear selection" in body  # the toggle names its next action


def test_add_screen_carries_the_selection_into_pagination(app, client):
    _photos(app, count=3)
    album_id = _album(app, "Trip")

    body = client.get(f"/albums/{album_id}/add?select=all&exclude_id=2").get_data(as_text=True)

    assert "2 selected" in body  # 3 matching less the exclusion
    assert "select=all" in body  # pagination links carry the selection
    assert "exclude_id=2" in body


def test_empty_album_offers_no_edit_mode(app, client):
    album_id = _album(app, "Trip")
    body = client.get(f"/albums/{album_id}").get_data(as_text=True)
    assert "?edit=1" not in body  # nothing to select


def test_delete_album_keeps_the_photos(app, client):
    _photos(app)
    album_id = _album(app, "Trip", items=[1, 2])

    resp = client.post(f"/albums/{album_id}/delete")

    assert resp.status_code == 302 and resp.headers["Location"].endswith("/albums")
    with app.app_context():
        assert db.session.get(Album, album_id) is None
        assert db.session.query(MediaItem).count() == 3  # the photos are untouched


def test_delete_unknown_album_404(app, client):
    assert client.post("/albums/999/delete").status_code == 404
