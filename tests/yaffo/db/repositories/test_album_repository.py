"""Repository tests for albums: CRUD, membership, the cover's fallback and
dangling-cover rules, and the bulk operations that back the UI's "select all
matching these filters" (which must add in one statement, not one per id)."""
from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from yaffo.db import db
from yaffo.db.models import AlbumItem, MediaItem
from yaffo.db.repositories import album_repository as repo

pytestmark = pytest.mark.unit


@pytest.fixture
def session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    db.metadata.create_all(engine)
    with Session(engine) as sess:
        yield sess
    engine.dispose()


@pytest.fixture
def photos(session):
    """Six photos across two years — 1-3 are 2024, 4-6 are 2023. year/month are
    denormalized columns on MediaItem (set at index time) and are what the filter
    layer matches on, so the fixture sets them alongside date_taken."""
    def photo(item_id: int, taken: datetime) -> MediaItem:
        return MediaItem(
            id=item_id,
            full_file_path=f"/lib/{item_id}.jpg",
            date_taken=taken,
            year=taken.year,
            month=taken.month,
        )

    items = [
        photo(1, datetime(2024, 7, 3)),
        photo(2, datetime(2024, 7, 2)),
        photo(3, datetime(2024, 7, 1)),
        photo(4, datetime(2023, 5, 3)),
        photo(5, datetime(2023, 5, 2)),
        photo(6, datetime(2023, 5, 1)),
    ]
    session.add_all(items)
    session.commit()
    return items


def test_create_and_update_album(session):
    album = repo.create_album(session, "  Summer 2024  ", description=" beach ")
    assert album.name == "Summer 2024" and album.description == "beach"

    repo.update_album(session, album.id, "Summer", description="")
    assert album.name == "Summer" and album.description is None


def test_album_names_are_unique(session):
    repo.create_album(session, "Summer")
    with pytest.raises(ValueError, match="already exists"):
        repo.create_album(session, "Summer")

    other = repo.create_album(session, "Winter")
    with pytest.raises(ValueError, match="already exists"):
        repo.update_album(session, other.id, "Summer")


def test_album_needs_a_name(session):
    with pytest.raises(ValueError, match="needs a name"):
        repo.create_album(session, "   ")


def test_add_items_appends_in_order_and_skips_duplicates(session, photos):
    album = repo.create_album(session, "Trip")

    assert repo.add_items(session, album.id, [3, 1]) == 2
    assert repo.add_items(session, album.id, [1, 2]) == 1  # 1 is already a member

    assert [item.id for item in repo.list_items(session, album.id)] == [3, 1, 2]


def test_add_items_ignores_unknown_media_items(session, photos):
    album = repo.create_album(session, "Trip")
    assert repo.add_items(session, album.id, [1, 999]) == 1
    assert [item.id for item in repo.list_items(session, album.id)] == [1]


def test_add_matching_adds_every_photo_matching_the_filters(session, photos):
    """The bulk path: the caller posts filters, not ids."""
    album = repo.create_album(session, "2024")

    added = repo.add_matching(session, album.id, {"year": 2024})

    assert added == 3
    # Newest first, mirroring the grid the user was looking at.
    assert [item.id for item in repo.list_items(session, album.id)] == [1, 2, 3]


def test_add_matching_skips_existing_members_and_appends_after_them(session, photos):
    album = repo.create_album(session, "Mixed")
    repo.add_items(session, album.id, [6])  # a 2023 photo, already a member

    assert repo.add_matching(session, album.id, {"year": 2023}) == 2  # 4 and 5, not 6

    assert [item.id for item in repo.list_items(session, album.id)] == [6, 4, 5]


def test_add_matching_with_no_filters_adds_the_whole_library(session, photos):
    album = repo.create_album(session, "Everything")
    assert repo.add_matching(session, album.id, {}) == 6


def test_add_matching_honors_exclusions(session, photos):
    """The "all except these" selection: the scope is the filter, minus what the
    user unticked while the whole scope was selected."""
    album = repo.create_album(session, "2024")

    added = repo.add_matching(session, album.id, {"year": 2024}, exclude_ids=[2])

    assert added == 2
    assert [item.id for item in repo.list_items(session, album.id)] == [1, 3]


def test_remove_all_honors_exclusions(session, photos):
    album = repo.create_album(session, "Trip")
    repo.add_items(session, album.id, [1, 2, 3])

    removed = repo.remove_all(session, album.id, exclude_ids=[2])

    assert removed == 2
    assert [item.id for item in repo.list_items(session, album.id)] == [2]


def test_remove_items_leaves_the_photos_alone(session, photos):
    album = repo.create_album(session, "Trip")
    repo.add_items(session, album.id, [1, 2, 3])

    assert repo.remove_items(session, album.id, [2]) == 1

    assert [item.id for item in repo.list_items(session, album.id)] == [1, 3]
    assert session.get(MediaItem, 2) is not None  # the photo itself survives


def test_remove_all_empties_the_album(session, photos):
    album = repo.create_album(session, "Trip")
    repo.add_items(session, album.id, [1, 2, 3])

    assert repo.remove_all(session, album.id) == 3

    assert repo.list_items(session, album.id) == []
    assert session.query(MediaItem).count() == 6


def test_cover_falls_back_to_the_first_member(session, photos):
    album = repo.create_album(session, "Trip")
    assert repo.cover_media_item(session, album) is None  # empty album

    repo.add_items(session, album.id, [3, 1])
    assert repo.cover_media_item(session, album).id == 3

    repo.set_cover(session, album.id, 1)
    assert repo.cover_media_item(session, album).id == 1


def test_cover_must_be_a_member(session, photos):
    album = repo.create_album(session, "Trip")
    repo.add_items(session, album.id, [1])
    with pytest.raises(ValueError, match="must be a member"):
        repo.set_cover(session, album.id, 2)


def test_removing_the_cover_photo_drops_back_to_the_fallback(session, photos):
    album = repo.create_album(session, "Trip")
    repo.add_items(session, album.id, [1, 2])
    repo.set_cover(session, album.id, 1)

    repo.remove_items(session, album.id, [1])

    assert album.cover_media_item_id is None  # no longer points outside the album
    assert repo.cover_media_item(session, album).id == 2


def test_reorder_persists_manual_order(session, photos):
    album = repo.create_album(session, "Trip")
    repo.add_items(session, album.id, [1, 2, 3])

    repo.reorder(session, album.id, [3, 1, 2])

    assert [item.id for item in repo.list_items(session, album.id)] == [3, 1, 2]


def test_reorder_ignores_ids_outside_the_album(session, photos):
    album = repo.create_album(session, "Trip")
    repo.add_items(session, album.id, [1, 2])

    repo.reorder(session, album.id, [2, 99, 1])

    assert [item.id for item in repo.list_items(session, album.id)] == [2, 1]


def test_delete_album_removes_membership_but_not_photos(session, photos):
    album = repo.create_album(session, "Trip")
    repo.add_items(session, album.id, [1, 2])

    assert repo.delete_album(session, album.id) is True

    assert repo.get_album(session, album.id) is None
    assert session.query(AlbumItem).count() == 0
    assert session.query(MediaItem).count() == 6


def test_item_counts_covers_every_album(session, photos):
    trip = repo.create_album(session, "Trip")
    empty = repo.create_album(session, "Empty")
    repo.add_items(session, trip.id, [1, 2])

    counts = repo.item_counts(session)

    assert counts.get(trip.id) == 2
    assert counts.get(empty.id) is None  # no rows; callers default to 0
    assert repo.item_count(session, empty.id) == 0
