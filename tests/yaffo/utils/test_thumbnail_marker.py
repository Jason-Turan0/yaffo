import pytest

from yaffo.utils.thumbnail_marker import (
    THUMBNAIL_DIR_MARKER,
    ensure_thumbnail_dir,
    in_marked_thumbnail_dir,
)

pytestmark = pytest.mark.unit


def test_ensure_thumbnail_dir_creates_dir_and_marker(tmp_path):
    thumbs = tmp_path / "a" / "thumbs"

    ensure_thumbnail_dir(thumbs)

    assert (thumbs / THUMBNAIL_DIR_MARKER).is_file()


def test_ensure_thumbnail_dir_keeps_existing_marker(tmp_path):
    (tmp_path / THUMBNAIL_DIR_MARKER).write_text("custom")

    ensure_thumbnail_dir(tmp_path)

    assert (tmp_path / THUMBNAIL_DIR_MARKER).read_text() == "custom"


def test_ensure_thumbnail_dir_tolerates_unwritable_marker(tmp_path, monkeypatch):
    def fail(*_args, **_kwargs):
        raise PermissionError("read-only")

    monkeypatch.setattr("pathlib.Path.write_text", fail)

    ensure_thumbnail_dir(tmp_path / "thumbs")

    assert (tmp_path / "thumbs").is_dir()


def test_in_marked_thumbnail_dir_checks_every_ancestor(tmp_path):
    thumbs = tmp_path / "library" / "thumbs"
    (thumbs / "deep" / "er").mkdir(parents=True)
    (thumbs / THUMBNAIL_DIR_MARKER).write_text("")

    assert in_marked_thumbnail_dir(thumbs / "deep" / "er" / "face.jpg")
    assert not in_marked_thumbnail_dir(tmp_path / "library" / "photo.jpg")


def test_cache_never_masks_a_marked_ancestor(tmp_path):
    # An unmarked directory cached first must not hide a marker further up.
    cache: dict = {}
    thumbs = tmp_path / "thumbs"
    (thumbs / "sub").mkdir(parents=True)
    (thumbs / THUMBNAIL_DIR_MARKER).write_text("")

    assert in_marked_thumbnail_dir(thumbs / "sub" / "a.jpg", cache)
    assert in_marked_thumbnail_dir(thumbs / "sub" / "b.jpg", cache)
    assert not in_marked_thumbnail_dir(tmp_path / "photo.jpg", cache)
