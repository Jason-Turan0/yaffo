"""Album host actions: an automation can create, rename, fill, prune and delete an
album. Reading albums needs no host call — `albums` / `album_items` are data_query
sources — so only the mutating half lives here.

The property that matters: automations RUN AGAIN (nightly, or on every import), so the
obvious script — "make sure album X exists, then add today's photos" — must be safe to
repeat.
"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from yaffo.background_tasks.automation_sandbox import automation_actions as actions
from yaffo.db import db
from yaffo.db.models import MediaItem
from yaffo.db.repositories import album_repository

pytestmark = pytest.mark.unit


@pytest.fixture
def session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    db.metadata.create_all(engine)
    with Session(engine) as sess:
        sess.add_all([
            MediaItem(id=1, full_file_path="/lib/1.jpg"),
            MediaItem(id=2, full_file_path="/lib/2.jpg"),
            MediaItem(id=3, full_file_path="/lib/3.jpg"),
        ])
        sess.commit()
        yield sess
    engine.dispose()


def test_create_album_returns_the_new_albums_id(session):
    album_id = actions.create_album(session, "Beach 2024", "Coast trip")

    album = album_repository.get_album(session, album_id)
    assert album.name == "Beach 2024"
    assert album.description == "Coast trip"


def test_create_album_is_idempotent_on_the_name(session):
    """A repeating automation calls this on every run: the second call must return the
    same album, not fail and not make a duplicate."""
    first = actions.create_album(session, "Beach 2024")
    second = actions.create_album(session, "Beach 2024", "a different description")

    assert second == first
    assert len(album_repository.list_albums(session)) == 1


def test_add_to_album_is_batched_and_repeatable(session):
    album_id = actions.create_album(session, "Beach 2024")

    actions.add_to_album(session, album_id, [1, 2])
    actions.add_to_album(session, album_id, [2, 3])  # 2 again — a re-run

    assert [item.id for item in album_repository.list_items(session, album_id)] == [1, 2, 3]


def test_remove_from_album_keeps_the_photos(session):
    album_id = actions.create_album(session, "Beach 2024")
    actions.add_to_album(session, album_id, [1, 2, 3])

    actions.remove_from_album(session, album_id, [2])
    actions.remove_from_album(session, album_id, [2])  # not a member any more — no-op

    assert [item.id for item in album_repository.list_items(session, album_id)] == [1, 3]
    assert session.query(MediaItem).count() == 3  # the photos are untouched


def test_update_album_renames_without_touching_membership(session):
    album_id = actions.create_album(session, "Beach")
    actions.add_to_album(session, album_id, [1, 2])

    actions.update_album(session, album_id, "Beach 2024", "Coast trip")

    album = album_repository.get_album(session, album_id)
    assert album.name == "Beach 2024" and album.description == "Coast trip"
    assert len(album_repository.list_items(session, album_id)) == 2


def test_delete_album_keeps_the_photos(session):
    album_id = actions.create_album(session, "Beach 2024")
    actions.add_to_album(session, album_id, [1, 2])

    actions.delete_album(session, album_id)

    assert album_repository.get_album(session, album_id) is None
    assert session.query(MediaItem).count() == 3  # deleting an album deletes no photos


def test_a_dry_run_records_album_mutations_without_performing_them(session):
    """Album actions are flagged mutating, so a test/preview of an automation records
    the call and changes nothing — the same contract the other write actions have."""
    from yaffo.background_tasks.automation_sandbox.automation_host import (
        build_recording_host_functions,
    )
    from yaffo.background_tasks.automation_sandbox.starlark_runner import run_starlark

    functions, calls = build_recording_host_functions(session)

    result = run_starlark(
        '''
album_id = create_album("Beach 2024")
add_to_album(album_id, [1, 2])
''',
        functions=functions,
    )

    assert result.success is True, result.error
    assert [call.name for call in calls] == ["create_album", "add_to_album"]
    assert album_repository.list_albums(session) == []  # nothing was actually created


def test_summaries_describe_the_call_for_the_test_ui(session):
    album_id = actions.create_album(session, "Beach 2024")

    assert actions.summarize_create_album(["Beach 2024"], session) == "Create album 'Beach 2024'"
    assert actions.summarize_add_to_album([album_id, [1, 2]], session) == "Add 2 photo(s) to an album"
    assert actions.summarize_remove_from_album([album_id, [1]], session) == "Remove 1 photo(s) from an album"
    assert actions.summarize_delete_album([album_id], session) == "Delete album 'Beach 2024'"
