"""POST /api/openings: show a file or folder on this computer, named by ids only.
Used by the media page's Open File / Open Folder and the assistant's open links."""
import pytest

from yaffo.db import db
from yaffo.db.models import MediaItem

pytestmark = pytest.mark.unit


@pytest.fixture
def opened(monkeypatch):
    """Record what the route would hand the OS instead of opening it."""
    calls = []
    monkeypatch.setattr("yaffo.routes.api.open_in_os", lambda path, reveal=False: calls.append((path, reveal)))
    return calls


@pytest.fixture
def media_root(tmp_path, monkeypatch):
    from types import SimpleNamespace
    root = tmp_path / "Photos"
    (root / "2019").mkdir(parents=True)
    (root / "2019" / "a.jpg").write_bytes(b"x")
    monkeypatch.setattr("yaffo.site_agents.assistant.file_targets.get_media_dir_entries",
                        lambda session: [SimpleNamespace(id="m1", path=root)])
    return root


def test_opening_opens_the_looked_up_path(client, opened, media_root):
    photo = media_root / "2019" / "a.jpg"
    item = MediaItem(full_file_path=str(photo))
    db.session.add(item)
    db.session.commit()

    assert client.post("/api/openings", json={"media_item_id": item.id, "show": "folder"}).status_code == 202
    assert client.post("/api/openings", json={"media_dir_id": "m1", "path": "2019", "show": "file"}).status_code == 202
    # A file shown "in its folder" is revealed; a folder just opens.
    assert opened == [(photo.resolve(), True), ((media_root / "2019").resolve(), False)]


@pytest.mark.parametrize("body", [
    {"media_dir_id": "m1", "path": "../..", "show": "file"},
    {"media_dir_id": "m1", "path": "/etc", "show": "file"},
    {"path": "/etc/passwd", "show": "file"},
    {"media_dir_id": "m1", "path": "missing.jpg", "show": "file"},
    {"media_item_id": 424242, "show": "file"},
    "not an object",
])
def test_opening_refuses_anything_else(client, opened, media_root, body):
    response = client.post("/api/openings", json=body)
    assert response.status_code == 404
    assert response.get_json()["code"] == "open_target_unavailable"
    assert opened == []


def test_opening_reports_when_the_os_cannot_open(client, media_root, monkeypatch):
    def fail(path, reveal=False):
        raise OSError("no handler")
    monkeypatch.setattr("yaffo.routes.api.open_in_os", fail)
    response = client.post("/api/openings", json={"media_dir_id": "m1", "path": "", "show": "file"})
    assert response.status_code == 500 and response.get_json()["code"] == "open_failed"


def test_opening_needs_no_assistant(client, opened, media_root, monkeypatch):
    """The media page opens files whether or not the assistant is turned on."""
    monkeypatch.setattr("yaffo.site_agents.assistant.settings.is_enabled", lambda session=None: False)
    photo = media_root / "2019" / "a.jpg"
    item = MediaItem(full_file_path=str(photo))
    db.session.add(item)
    db.session.commit()

    assert client.post("/api/openings", json={"media_item_id": item.id, "show": "file"}).status_code == 202
    assert opened == [(photo.resolve(), False)]


def test_a_raw_path_is_refused(client, opened):
    response = client.post("/api/openings", json={"path": "/Applications/Calculator.app", "show": "file"})
    assert response.status_code == 404
    assert opened == []


def test_a_form_post_is_refused(client, opened):
    response = client.post("/api/openings", data={"media_item_id": "1", "show": "file"})
    assert response.status_code == 415
    assert opened == []
