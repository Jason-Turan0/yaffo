"""The home page flashes a warning when a configured media folder is missing on
disk — the symptom of an unplugged external drive, which otherwise just shows a
grid of broken images."""
import json

from yaffo.db import db
from yaffo.db.models import ApplicationSettings


def _set_media_dirs(paths: list[str]) -> None:
    entries = [{"id": f"dir-{i}", "path": path} for i, path in enumerate(paths)]
    db.session.add(ApplicationSettings(name="media_dirs", type="json", value=json.dumps(entries)))
    db.session.commit()


def test_warns_when_media_dir_missing(app, client, tmp_path):
    missing = tmp_path / "unplugged-drive"
    _set_media_dirs([str(missing)])

    body = client.get("/").data.decode()

    assert "Media folders are not available" in body
    assert str(missing) in body


def test_no_warning_when_media_dirs_exist(app, client, tmp_path):
    existing = tmp_path / "photos"
    existing.mkdir()
    _set_media_dirs([str(existing)])

    body = client.get("/").data.decode()

    assert "Media folders are not available" not in body


def test_no_warning_when_no_media_dirs_configured(app, client):
    body = client.get("/").data.decode()

    assert "Media folders are not available" not in body