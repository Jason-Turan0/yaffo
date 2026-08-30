import json
from pathlib import Path

import pytest

from yaffo.db import db
from yaffo.db.models import JOB_STATUS_COMPLETED, Job, JobResult
from yaffo.routes.utilities import remove_duplicates as mod

pytestmark = pytest.mark.unit


def test_collect_media_paths_includes_photos_and_videos(monkeypatch, tmp_path):
    photo = tmp_path / "photo.jpg"
    video = tmp_path / "video.mp4"
    ignored = tmp_path / "notes.txt"
    photo.touch()
    video.touch()
    ignored.touch()
    monkeypatch.setattr(mod, "get_thumbnail_dir", lambda: None)
    monkeypatch.setattr(mod, "is_system_file", lambda name: False)

    paths = set(mod.collect_media_paths([str(tmp_path)]))

    assert paths == {str(photo), str(video)}


def test_collect_media_paths_is_sorted_for_stable_default_keepers(monkeypatch, tmp_path):
    later = tmp_path / "z-photo.jpg"
    earlier = tmp_path / "a-photo.jpg"
    later.touch()
    earlier.touch()
    monkeypatch.setattr(mod, "get_thumbnail_dir", lambda: None)
    monkeypatch.setattr(mod, "is_system_file", lambda name: False)

    assert mod.collect_media_paths([str(tmp_path)]) == [str(earlier), str(later)]


def test_collect_media_paths_supports_all_cataloged_video_extensions(monkeypatch, tmp_path):
    videos = [tmp_path / f"clip{extension}" for extension in (".mp4", ".mov", ".m4v", ".avi", ".mkv", ".wmv", ".flv")]
    for video in videos:
        video.touch()
    monkeypatch.setattr(mod, "get_thumbnail_dir", lambda: None)
    monkeypatch.setattr(mod, "is_system_file", lambda name: False)

    paths = set(mod.collect_media_paths([str(tmp_path)]))

    assert paths == {str(video) for video in videos}


def test_page_translates_duplicate_configuration(client):
    client.post("/settings/locale", data={"locale": "de"})

    body = client.get("/utilities/remove-duplicates").get_data(as_text=True)

    assert "<title>Duplikate entfernen - Werkzeuge - Yaffo</title>" in body
    assert "Doppelte Fotos und Videos finden und entfernen" in body
    assert "Medien insgesamt" in body
    assert "Verzeichnisse" in body
    assert "Konfiguration" in body
    assert "Kein Verzeichnis ausgewählt" in body
    assert "Verzeichnisse erneut durchsuchen" in body
    assert "+ Weiteres Verzeichnis hinzufügen" in body
    assert "Duplikate finden" in body


def test_start_validation_uses_saved_locale_and_error_code(client):
    client.post("/settings/locale", data={"locale": "de"})

    response = client.post("/utilities/remove-duplicates/start", data={})

    assert response.status_code == 400
    assert response.get_json() == {
        "error": "Mindestens ein Verzeichnis ist erforderlich",
        "code": "directory_required",
    }


def test_results_translate_duplicate_groups(app, client, tmp_path):
    first = tmp_path / "first.jpg"
    second = tmp_path / "second.jpg"
    first.touch()
    second.touch()
    with app.app_context():
        job = Job(
            id="duplicates-job",
            name="find_duplicates",
            status=JOB_STATUS_COMPLETED,
            task_count=2,
        )
        result = JobResult(
            job=job,
            task_id="duplicates-result",
            result_data=json.dumps([{
                "id": 0,
                "paths": [str(first), str(second)],
            }]),
        )
        db.session.add_all([job, result])
        db.session.commit()

    client.post("/settings/locale", data={"locale": "de"})
    body = client.get(
        "/utilities/remove-duplicates/results/duplicates-job",
    ).get_data(as_text=True)

    assert "<title>Duplikatergebnisse - Werkzeuge - Yaffo</title>" in body
    assert "Doppelte Medien gefunden" in body
    assert "Doppelte Fotos und Videos prüfen und verwalten" in body
    assert "Medien insgesamt verarbeitet" in body
    assert "Duplikatgruppen gefunden" in body
    assert "Duplikate ausgewählt" in body
    assert "In den Papierkorb verschieben" in body
    assert "Dauerhaft löschen" in body
    assert "Ausgewählte Duplikate entfernen" in body
    assert "Gruppe 1" in body
    assert 'alt="Doppeltes Foto"' in body


def test_execute_validation_translates_notification(client):
    client.post("/settings/locale", data={"locale": "de"})

    response = client.post("/utilities/remove-duplicates/execute/missing", data={})
    notification = json.loads(response.headers["HX-Trigger"])["showNotification"]

    assert response.status_code == 400
    assert response.get_json() == {
        "error": "Keine Dateien ausgewählt",
        "code": "files_required",
    }
    assert notification == {
        "message": "Keine Dateien ausgewählt",
        "type": "error",
    }


def test_execute_success_uses_saved_locale_and_plural(app, client, monkeypatch, tmp_path):
    first = tmp_path / "first.jpg"
    second = tmp_path / "second.jpg"
    first.touch()
    second.touch()
    with app.app_context():
        job = Job(
            id="duplicates-job",
            name="find_duplicates",
            status=JOB_STATUS_COMPLETED,
            task_count=2,
        )
        result = JobResult(
            job=job,
            task_id="duplicates-result",
            result_data=json.dumps([{
                "id": 0,
                "paths": [str(first), str(second)],
            }]),
        )
        db.session.add_all([job, result])
        db.session.commit()

    calls = []
    monkeypatch.setattr(
        "yaffo.routes.utilities.remove_duplicates.remove_duplicates_task",
        lambda **kwargs: calls.append(kwargs),
    )
    client.post("/settings/locale", data={"locale": "de"})

    response = client.post(
        "/utilities/remove-duplicates/execute/duplicates-job",
        data={"selected_photo": "2", "action_type": "trash"},
    )
    notification = json.loads(response.headers["HX-Trigger"])["showNotification"]

    assert response.status_code == 200
    assert response.get_json()["job_id"]
    assert notification == {
        "message": "Entfernen von 1 Duplikat wurde gestartet",
        "type": "success",
    }
    assert calls[0]["file_paths"] == [str(second)]
