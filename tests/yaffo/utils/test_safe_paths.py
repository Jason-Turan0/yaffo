from __future__ import annotations

import pytest

from yaffo.utils.safe_paths import PathOutsideAllowedRoots, resolve_path_in_roots

pytestmark = pytest.mark.unit


def test_resolves_file_inside_allowed_root(tmp_path):
    root = tmp_path / "media"
    root.mkdir()
    photo = root / "trip" / "photo.jpg"
    photo.parent.mkdir()
    photo.write_bytes(b"photo")

    resolved, matched_root = resolve_path_in_roots(photo, [root])

    assert resolved == photo.resolve()
    assert matched_root == root.resolve()


def test_rejects_parent_traversal_outside_root(tmp_path):
    root = tmp_path / "media"
    root.mkdir()
    outside = tmp_path / "secret.txt"
    outside.write_text("secret")

    with pytest.raises(PathOutsideAllowedRoots):
        resolve_path_in_roots(root / ".." / "secret.txt", [root])


def test_rejects_symlink_that_escapes_root(tmp_path):
    root = tmp_path / "media"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "secret.txt"
    secret.write_text("secret")
    link = root / "linked"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlinks unavailable: {exc}")

    with pytest.raises(PathOutsideAllowedRoots):
        resolve_path_in_roots(link / "secret.txt", [root])


def test_nonexistent_write_target_still_resolves_existing_parent_symlinks(tmp_path):
    root = tmp_path / "downloads"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    link = root / "linked"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlinks unavailable: {exc}")

    with pytest.raises(PathOutsideAllowedRoots):
        resolve_path_in_roots(link / "new-file.jpg", [root], must_exist=False)
