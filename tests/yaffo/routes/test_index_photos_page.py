"""Route tests for the Index Photos page: the page renders without scanning, and the
scan endpoint streams the NDJSON contract (progress records, then a done record)."""
import json

import pytest

from yaffo.db import db
from yaffo.db.models import Automation
from types import SimpleNamespace

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


def test_reindex_library_forces_every_indexed_file(app, client, monkeypatch, tmp_path):
    """Reindex rebuilds what's already there — so it enqueues with force, which is what
    makes indexing re-run on files Sync would skip."""
    from yaffo.db import db
    from yaffo.db.models import MediaItem

    present = tmp_path / "present.jpg"
    present.write_bytes(b"\xff\xd8\xff")
    with app.app_context():
        db.session.add_all([
            MediaItem(full_file_path=str(present)),
            MediaItem(full_file_path=str(tmp_path / "deleted.jpg")),  # gone from disk
        ])
        db.session.commit()

    calls = {}
    monkeypatch.setattr("yaffo.routes.utilities.index_photos.get_thumbnail_dir", lambda: tmp_path / "thumbs")
    monkeypatch.setattr(
        "yaffo.routes.utilities.index_photos.reindex_media_items",
        lambda session, items: calls.update(paths=[item.full_file_path for item in items])
        or SimpleNamespace(import_job_id="i1", index_job_id="x1"),
    )

    response = client.post("/utilities/index-photos/reindex")

    assert response.status_code == 202
    assert response.get_json() == {"job_id": "x1", "media_item_count": 1}
    # The file that no longer exists is left out — indexing it would only error, and
    # reconciling deletions is Sync's job, not this one's.
    assert calls["paths"] == [str(present)]


def test_reindex_library_rejects_an_empty_library(app, client, monkeypatch, tmp_path):
    monkeypatch.setattr("yaffo.routes.utilities.index_photos.get_thumbnail_dir", lambda: tmp_path / "thumbs")

    response = client.post("/utilities/index-photos/reindex")

    assert response.status_code == 400
    assert response.get_json()["code"] == "library_empty"


def test_page_offers_the_reindex_button(client, monkeypatch, tmp_path):
    media = tmp_path / "media"
    media.mkdir()
    monkeypatch.setattr("yaffo.routes.utilities.index_photos.get_media_dirs", lambda: [media])
    monkeypatch.setattr("yaffo.routes.utilities.index_photos.get_thumbnail_dir", lambda: tmp_path / "thumbs")

    body = client.get("/utilities/index-photos").data.decode()

    assert 'id="reindex-button"' in body
    assert "Reindex Library" in body


def _add_job(app, job_id, name, status, minutes_ago, **fields):
    from datetime import datetime, timedelta
    from yaffo.db.models import Job
    created = datetime(2026, 9, 1, 12, 0) - timedelta(minutes=minutes_ago)
    with app.app_context():
        db.session.add(Job(
            id=job_id, name=name, status=status, created_at=created, started_at=created,
            task_count=fields.pop("task_count", 10), completed_count=fields.pop("completed_count", 10),
            cancelled_count=0, error_count=fields.pop("error_count", 0),
            message=fields.pop("message", "Indexed {totalCount}/{taskCount} photos"), **fields,
        ))
        db.session.commit()


def test_page_shows_only_in_progress_latest_runs_as_cards(app, client):
    _add_job(app, "index-old", "index_photos", "COMPLETED", 60, error_count=3)
    _add_job(app, "index-new", "index_photos", "RUNNING", 5, completed_count=4)
    _add_job(app, "import-stuck", "import_photos", "RUNNING", 70,
             message="Imported {totalCount}/{taskCount} photos")
    _add_job(app, "import-new", "import_photos", "COMPLETED", 6,
             message="Imported {totalCount}/{taskCount} photos")
    _add_job(app, "dupes", "find_duplicates", "RUNNING", 1)

    body = client.get("/utilities/index-photos").get_data(as_text=True)
    cards, history = body.split('class="section index-run-history"')

    # A card only for a kind whose latest run is still in progress.
    assert 'id="job-index-new"' in cards
    assert 'id="job-import-new"' not in cards and 'id="job-import-stuck"' not in cards
    assert 'id="job-index-old"' not in cards
    # Everything else is history: a finished latest run, an older one, and an
    # older run that never finished. Labelled by kind; other jobs aren't listed.
    assert "3 errors" in history
    assert "Index photos" in history and "Import photos" in history
    assert history.count('class="run-history-row') == 3
    assert "dupes" not in body
    # A card that finishes while the page is open gets no Dismiss (it deletes the job).
    assert "/jobs/index-new/fragment?has_results=0&amp;dismiss=0" in cards
    assert "hasActiveJobs: true" in body


def test_page_shows_no_cards_when_nothing_is_in_progress(app, client):
    _add_job(app, "index-done", "index_photos", "COMPLETED", 5)
    body = client.get("/utilities/index-photos").get_data(as_text=True)
    assert 'id="job-progress-section"' not in body
    assert "Run history" in body
    assert "hasActiveJobs: false" in body


def test_page_reports_active_jobs_from_running_runs(app, client):
    _add_job(app, "index-running", "index_photos", "RUNNING", 1, completed_count=4)
    body = client.get("/utilities/index-photos").get_data(as_text=True)
    assert 'id="job-index-running"' in body
    # Its polling asks for no Dismiss once it finishes, and so does Cancel.
    assert "/jobs/index-running/fragment?has_results=0&amp;dismiss=0" in body
    assert '"dismiss": false' in body
    assert "hasActiveJobs: true" in body
    assert "Run history" not in body


def test_job_card_shows_error_count_and_message(app, client):
    _add_job(app, "index-errors", "index_photos", "RUNNING", 1, error_count=7,
             error="Could not read IMG_0412.HEIC")
    body = client.get("/utilities/index-photos").get_data(as_text=True)
    assert '<span class="job-error-count">7 errors</span>' in body
    assert '<p class="job-error-message">Could not read IMG_0412.HEIC</p>' in body


def test_job_fragment_omits_dismiss_when_asked(app, client):
    _add_job(app, "index-done", "index_photos", "COMPLETED", 1)
    assert "/jobs/index-done/delete" in client.get("/jobs/index-done/fragment").get_data(as_text=True)
    body = client.get("/jobs/index-done/fragment?dismiss=0").get_data(as_text=True)
    assert "/jobs/index-done/delete" not in body


def test_job_card_status_reads_like_the_run_history(app, client):
    _add_job(app, "index-live", "index_photos", "RUNNING", 1, completed_count=4)
    body = client.get("/utilities/index-photos").get_data(as_text=True)
    assert '<span class="chip chip-warning job-status">Running</span>' in body
    assert ">RUNNING<" not in body

    fragment = client.get("/jobs/index-live/fragment").get_data(as_text=True)
    assert '<span class="chip chip-warning job-status">Running</span>' in fragment


def test_finished_job_card_with_errors_says_so(app, client):
    _add_job(app, "index-done", "index_photos", "COMPLETED", 1, error_count=2)
    fragment = client.get("/jobs/index-done/fragment").get_data(as_text=True)
    assert '<span class="chip chip-warning job-status">Completed with errors</span>' in fragment
