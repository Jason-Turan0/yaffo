"""Resolution tests for the media-dir-aware query pieces (media_dir_repository):
the calculated-column path filters (media_dir_id / relative_path) translated into
full_file_path SQL via resolve_query, and the media_dirs / folders virtual sources.

A real media dir is configured in settings and photos are seeded with absolute
full_file_path values under it, so the relative<->absolute mapping is exercised
end to end against a throwaway SQLite DB.
"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from yaffo.db import db
from yaffo.db.models import MediaItem
from yaffo.db.repositories import data_query_repository as dq
from yaffo.db.repositories import media_dir_repository as mdr
from yaffo.db.repositories.media_dir_repository import add_media_dir

pytestmark = pytest.mark.unit


@pytest.fixture
def ctx(tmp_path):
    """A DB with one configured media dir and photos seeded under it. Returns
    (session, media_dir_id, root)."""
    root = (tmp_path / "lib").resolve()
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    db.metadata.create_all(engine)
    with Session(engine) as sess:
        media_dir = add_media_dir(sess, str(root))
        sess.add_all([
            MediaItem(id=1, full_file_path=str(root / "top.jpg")),
            MediaItem(id=2, full_file_path=str(root / "2024" / "jan" / "a.jpg")),
            MediaItem(id=3, full_file_path=str(root / "2024" / "jan" / "b.jpg")),
            MediaItem(id=4, full_file_path=str(root / "2024" / "feb" / "c.jpg")),
            MediaItem(id=5, full_file_path=str(root / "2023" / "d.jpg")),
        ])
        sess.commit()
        yield sess, media_dir.id, root
    engine.dispose()


def _ids(rows):
    return {r["id"] for r in rows}


class TestMediaDirsSource:
    def test_lists_configured_dirs_as_id_name(self, ctx):
        session, media_dir_id, root = ctx
        assert dq.resolve_query(session, {"source": "media_dirs"}) == [
            {"id": media_dir_id, "name": "lib"}
        ]


class TestFoldersSource:
    def test_root_lists_immediate_subfolders_with_recursive_counts(self, ctx):
        session, media_dir_id, root = ctx
        # top.jpg sits directly in the root, so it isn't a subfolder; 2024 counts all 3.
        assert dq.resolve_query(session, {"source": "folders", "media_dir_id": media_dir_id}) == [
            {"name": "2023", "photo_count": 1},
            {"name": "2024", "photo_count": 3},
        ]

    def test_nested_path_lists_its_subfolders(self, ctx):
        session, media_dir_id, root = ctx
        assert dq.resolve_query(session, {"source": "folders", "media_dir_id": media_dir_id, "path": "2024"}) == [
            {"name": "feb", "photo_count": 1},
            {"name": "jan", "photo_count": 2},
        ]

    def test_unknown_media_dir_is_empty(self, ctx):
        session, _media_dir_id, _root = ctx
        assert dq.resolve_query(session, {"source": "folders", "media_dir_id": "nope"}) == []

    def test_path_escape_is_empty(self, ctx):
        session, media_dir_id, _root = ctx
        assert dq.resolve_query(session, {"source": "folders", "media_dir_id": media_dir_id, "path": "../.."}) == []


class TestPathFilters:
    def test_media_dir_id_matches_every_photo_under_it(self, ctx):
        session, media_dir_id, _root = ctx
        rows = dq.resolve_query(session, {"source": "media_items", "media_dir_id": {"eq": media_dir_id}})
        assert _ids(rows) == {1, 2, 3, 4, 5}

    def test_relative_path_prefix_matches_the_subtree(self, ctx):
        session, media_dir_id, _root = ctx
        rows = dq.resolve_query(session, {
            "source": "media_items", "media_dir_id": {"eq": media_dir_id}, "relative_path": {"prefix": "2024/"}
        })
        assert _ids(rows) == {2, 3, 4}

    def test_relative_path_prefix_with_count(self, ctx):
        session, media_dir_id, _root = ctx
        n = dq.resolve_query(session, {
            "source": "media_items", "op": "count",
            "media_dir_id": {"eq": media_dir_id}, "relative_path": {"prefix": "2024/jan/"}
        })
        assert n == 2

    def test_relative_path_eq_matches_one_file(self, ctx):
        session, media_dir_id, _root = ctx
        rows = dq.resolve_query(session, {
            "source": "media_items", "media_dir_id": {"eq": media_dir_id}, "relative_path": {"eq": "2023/d.jpg"}
        })
        assert _ids(rows) == {5}

    def test_unknown_media_dir_matches_nothing(self, ctx):
        session, _media_dir_id, _root = ctx
        rows = dq.resolve_query(session, {"source": "media_items", "media_dir_id": {"eq": "nope"}})
        assert rows == []

    def test_relative_path_with_multiple_dirs_is_rejected(self, ctx):
        session, media_dir_id, _root = ctx
        with pytest.raises(ValueError, match="single media_dir_id"):
            dq.resolve_query(session, {
                "source": "media_items", "media_dir_id": {"in": [media_dir_id, "other"]},
                "relative_path": {"prefix": "2024/"}
            })

    def test_path_filters_do_not_leak_full_file_path(self, ctx):
        session, media_dir_id, _root = ctx
        rows = dq.resolve_query(session, {"source": "media_items", "media_dir_id": {"eq": media_dir_id}, "limit": 1})
        assert "full_file_path" not in rows[0]
