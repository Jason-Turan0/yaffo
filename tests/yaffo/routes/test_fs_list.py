"""Route test for the folder-picker's filesystem listing endpoint."""
import pytest

from yaffo.db import db
from yaffo.db.repositories import media_dir_repository
from yaffo.utils import file_system

pytestmark = pytest.mark.unit


def test_fs_list_returns_listing_for_a_path(client, tmp_path):
    # Own subdir so the conftest's test.db files in tmp_path don't leak into the listing.
    tree = tmp_path / "tree"
    tree.mkdir()
    (tree / "sub").mkdir()
    (tree / "f.txt").write_text("x")

    resp = client.get("/api/fs/list", query_string={"path": str(tree), "mode": "folder"})

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["path"] == str(tree)
    assert data["parent"] == str(tmp_path)
    assert [e["name"] for e in data["entries"]] == ["sub"]  # folder mode: no files
    assert any(r["name"] == "Home" for r in data["roots"])


def test_fs_list_file_mode_includes_files(client, tmp_path):
    tree = tmp_path / "tree"
    tree.mkdir()
    (tree / "sub").mkdir()
    (tree / "f.txt").write_text("x")

    resp = client.get("/api/fs/list", query_string={"path": str(tree), "mode": "file"})

    names = [e["name"] for e in resp.get_json()["entries"]]
    assert names == ["f.txt", "sub"]


def test_fs_list_any_mode_includes_files(client, tmp_path):
    tree = tmp_path / "tree"
    tree.mkdir()
    (tree / "sub").mkdir()
    (tree / "f.txt").write_text("x")

    resp = client.get("/api/fs/list", query_string={"path": str(tree), "mode": "any"})

    names = [e["name"] for e in resp.get_json()["entries"]]
    assert names == ["f.txt", "sub"]


def test_fs_list_defaults_to_home_without_path(client):
    from pathlib import Path
    resp = client.get("/api/fs/list")
    assert resp.status_code == 200
    assert resp.get_json()["path"] == str(Path.home())


def test_fs_list_includes_configured_media_dirs(app, client, tmp_path):
    media_dir = tmp_path / "library"
    media_dir.mkdir()
    with app.app_context():
        media_dir_repository.add_media_dir(db.session, str(media_dir))

    resp = client.get("/api/fs/list")

    roots = {root["path"]: root for root in resp.get_json()["roots"]}
    assert roots[str(media_dir)]["name"] == "library"


def test_external_volume_shortcuts_include_mounted_directories(tmp_path):
    volumes = tmp_path / "Volumes"
    volumes.mkdir()
    (volumes / "Camera Card").mkdir()
    (volumes / ".hidden").mkdir()
    (volumes / "readme.txt").write_text("x")

    roots = file_system._external_volume_roots(volumes)

    assert [(root.name, root.path) for root in roots] == [("Camera Card", str(volumes / "Camera Card"))]


def test_fs_create_folder_creates_child_and_returns_path(client, tmp_path):
    parent = tmp_path / "parent"
    parent.mkdir()

    resp = client.post("/api/fs/create-folder", json={"path": str(parent), "name": "New Folder"})

    assert resp.status_code == 201
    created = parent / "New Folder"
    assert created.is_dir()
    assert resp.get_json()["path"] == str(created)


@pytest.mark.parametrize("name", ["", ".", "..", "nested/folder", "nested\\folder"])
def test_fs_create_folder_rejects_invalid_names(client, tmp_path, name):
    parent = tmp_path / "parent"
    parent.mkdir()

    resp = client.post("/api/fs/create-folder", json={"path": str(parent), "name": name})

    assert resp.status_code == 400
    assert "error" in resp.get_json()
