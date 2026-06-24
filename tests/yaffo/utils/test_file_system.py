"""Directory-listing service behind the in-app folder/file picker."""
import pytest

from yaffo.utils.file_system import DirListing, list_directory, listing_to_dict

pytestmark = pytest.mark.unit


def _make_tree(base):
    (base / "alpha").mkdir()
    (base / "beta").mkdir()
    (base / ".hidden").mkdir()
    (base / "note.txt").write_text("x")
    (base / ".secret").write_text("x")


def test_folder_mode_lists_only_visible_subdirs(tmp_path):
    _make_tree(tmp_path)
    listing = list_directory(str(tmp_path), "folder")
    names = [e.name for e in listing.entries]
    assert names == ["alpha", "beta"]  # sorted, no files, no dotfiles
    assert all(e.is_dir for e in listing.entries)


def test_file_mode_includes_visible_files_too(tmp_path):
    _make_tree(tmp_path)
    listing = list_directory(str(tmp_path), "file")
    names = [e.name for e in listing.entries]
    assert names == ["alpha", "beta", "note.txt"]  # dirs + files, dotfiles excluded
    file_entry = next(e for e in listing.entries if e.name == "note.txt")
    assert file_entry.is_dir is False


def test_parent_points_up_and_path_echoes(tmp_path):
    child = tmp_path / "child"
    child.mkdir()
    listing = list_directory(str(child), "folder")
    assert listing.path == str(child)
    assert listing.parent == str(tmp_path)


def test_missing_path_falls_back_to_home(tmp_path):
    listing = list_directory(str(tmp_path / "does-not-exist"), "folder")
    from pathlib import Path
    assert listing.path == str(Path.home())  # invalid path -> home, never an error


def test_unknown_mode_defaults_to_folder(tmp_path):
    _make_tree(tmp_path)
    listing = list_directory(str(tmp_path), "bogus")
    assert all(e.is_dir for e in listing.entries)


def test_roots_include_home(tmp_path):
    listing = list_directory(str(tmp_path), "folder")
    assert any(r.name == "Home" for r in listing.roots)


def test_listing_to_dict_is_json_shaped(tmp_path):
    _make_tree(tmp_path)
    payload = listing_to_dict(list_directory(str(tmp_path), "folder"))
    assert set(payload) == {"path", "parent", "error", "entries", "roots"}
    assert payload["entries"][0] == {"name": "alpha", "path": str(tmp_path / "alpha"), "is_dir": True}
