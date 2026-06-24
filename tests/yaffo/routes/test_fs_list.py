"""Route test for the folder-picker's filesystem listing endpoint."""
import pytest

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
