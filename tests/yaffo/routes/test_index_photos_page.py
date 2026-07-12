"""Route tests for the Index Photos page: the page renders without scanning, and the
scan endpoint streams the NDJSON contract (progress records, then a done record)."""
import json

import pytest

from yaffo.db import db
from yaffo.db.models import Automation
from yaffo.utils.file_sync import MediaScan

pytestmark = pytest.mark.unit


def test_page_renders_shell_without_scanning(app, client, monkeypatch, tmp_path):
    # The GET must not run the (slow) scan — guard by making iter_media_scan explode
    # if it's ever called during the page render.
    monkeypatch.setattr(
        "yaffo.routes.utilities.index_photos.iter_media_scan",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("page render must not scan")),
    )
    media = tmp_path / "organized"
    media.mkdir()
    monkeypatch.setattr("yaffo.routes.utilities.index_photos.get_media_dirs", lambda: [media])
    monkeypatch.setattr("yaffo.routes.utilities.index_photos.get_thumbnail_dir", lambda: tmp_path / "thumbs")

    resp = client.get("/utilities/index-photos")
    assert resp.status_code == 200
    assert b'id="scan-results"' in resp.data        # shell present for JS to fill
    assert b'id="stat-total-filesystem"' in resp.data


def test_page_translates_shared_utilities_navigation(app, client):
    with app.app_context():
        db.session.add_all([
            Automation(
                slug="system-task",
                name="System task",
                is_system=True,
                enabled=True,
            ),
            Automation(
                slug="custom-task",
                name="Custom task",
                is_system=False,
            ),
        ])
        db.session.commit()

    client.post("/settings/locale", data={"locale": "de"})
    body = client.get("/utilities/index-photos").get_data(as_text=True)

    assert "<h2>Werkzeuge</h2>" in body
    assert "Fotos indizieren" in body
    assert "Duplikate entfernen" in body
    assert "<h2>Automatisierungen</h2>" in body
    assert ">System</h3>" in body
    assert ">Benutzerdefiniert</h3>" in body
    assert "Neue Automatisierung" in body
    assert 'placeholder="z. B. Strandfotos taggen, Wöchentliche Bereinigung"' in body
    assert '<span class="chip chip-success">An</span>' in body
    assert "System task" in body
    assert "Custom task" in body


def test_page_translates_indexing_content(client, monkeypatch, tmp_path):
    media = tmp_path / "Fotos"
    media.mkdir()
    thumbnail_dir = tmp_path / "Vorschaubilder"
    monkeypatch.setattr(
        "yaffo.routes.utilities.index_photos.get_media_dirs",
        lambda: [media],
    )
    monkeypatch.setattr(
        "yaffo.routes.utilities.index_photos.get_thumbnail_dir",
        lambda: thumbnail_dir,
    )
    client.post("/settings/locale", data={"locale": "de"})

    body = client.get("/utilities/index-photos").get_data(as_text=True)

    assert "<title>Fotos indizieren - Werkzeuge - Yaffo</title>" in body
    assert "Fotos im Dateisystem mit der Datenbank vergleichen" in body
    assert "Datenbank synchronisieren" in body
    assert "Gesamtzahl im Dateisystem" in body
    assert "In die Datenbank importiert" in body
    assert "In der Datenbank indiziert" in body
    assert "Nicht indiziert" in body
    assert "Verwaist in der Datenbank" in body
    assert f"Miniaturansichtsverzeichnis ist nicht vorhanden: {thumbnail_dir}" in body


def test_scan_stream_emits_progress_then_done(app, client, monkeypatch):
    fake = MediaScan(
        unindexed=[{"filename": "b.jpg", "full_path": "/m/b.jpg"}],
        orphaned=[],
        total_imported=2,
        total_indexed=1,
        total_filesystem=2,
    )
    monkeypatch.setattr(
        "yaffo.routes.utilities.index_photos.iter_media_scan",
        lambda *a, **k: iter([7, fake]),
    )

    resp = client.get("/utilities/index-photos/scan")
    assert resp.status_code == 200
    assert resp.mimetype == "application/x-ndjson"
    assert resp.headers.get("Cache-Control") == "no-store"  # live data, never cached

    records = [json.loads(line) for line in resp.get_data(as_text=True).splitlines() if line.strip()]
    assert records[0] == {"type": "progress", "scanned": 7}
    assert records[1]["type"] == "done"
    assert records[1]["total_filesystem"] == 2
    assert records[1]["unindexed"] == [{"filename": "b.jpg", "full_path": "/m/b.jpg"}]
    assert records[1]["orphaned"] == []


def test_scan_stream_reports_error_as_record(app, client, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("disk unplugged")
        yield  # make it a generator

    monkeypatch.setattr("yaffo.routes.utilities.index_photos.iter_media_scan", boom)
    client.post("/settings/locale", data={"locale": "de"})

    resp = client.get("/utilities/index-photos/scan")
    assert resp.status_code == 200
    records = [json.loads(line) for line in resp.get_data(as_text=True).splitlines() if line.strip()]
    assert records[-1] == {
        "type": "error",
        "message": "Das Dateisystem konnte nicht durchsucht werden",
        "code": "filesystem_scan_failed",
    }


def test_sync_validation_uses_saved_locale_and_error_code(client, monkeypatch):
    monkeypatch.setattr(
        "yaffo.routes.utilities.index_photos.get_media_dirs",
        lambda: [],
    )
    client.post("/settings/locale", data={"locale": "de"})

    response = client.post("/utilities/index-photos/sync", json={})

    assert response.status_code == 400
    assert response.get_json() == {
        "error": "Keine Medienverzeichnisse konfiguriert",
        "code": "media_directories_not_configured",
    }
