import pytest

from yaffo.db import db
from yaffo.db.models import (
    FACE_STATUS_IGNORED,
    FACE_STATUS_UNASSIGNED,
    ApplicationSettings,
    Face,
    MediaItem,
)
from yaffo.i18n import LOCALE_SETTING

pytestmark = pytest.mark.unit


def test_accept_language_selects_supported_locale(client):
    response = client.get("/", headers={"Accept-Language": "de-DE,de;q=0.9,en;q=0.5"})

    body = response.get_data(as_text=True)
    assert '<html lang="de" dir="ltr"' in body
    assert 'locale: "de"' in body


def test_saved_locale_overrides_accept_language(app, client):
    with app.app_context():
        db.session.add(ApplicationSettings(name=LOCALE_SETTING, type="string", value="en"))
        db.session.commit()

    response = client.get("/", headers={"Accept-Language": "de"})

    assert '<html lang="en" dir="ltr"' in response.get_data(as_text=True)


def test_settings_locale_persists_and_renders_gettext(client, app):
    response = client.post("/settings/locale", data={"locale": "de"})

    assert response.status_code == 302
    with app.app_context():
        assert db.session.query(ApplicationSettings).filter_by(name=LOCALE_SETTING).one().value == "de"

    body = client.get("/settings").get_data(as_text=True)
    assert '<html lang="de" dir="ltr"' in body
    assert "<h2>Sprache</h2>" in body
    assert "Anwendungssprache" in body
    assert ">Speichern</button>" in body


def test_saved_locale_translates_shared_shell_and_components(client):
    client.post("/settings/locale", data={"locale": "de"})

    body = client.get("/").get_data(as_text=True)

    assert ">Startseite</a>" in body
    assert ">Gesichter</a>" in body
    assert ">Einstellungen</a>" in body
    assert "Ordner auswählen" in body
    assert "Diesen Ordner auswählen" in body
    assert 'aria-label="Schließen"' in body
    assert "Filter anwenden" in body
    assert "Filter zurücksetzen" in body
    assert "Datei- oder Ordnernamen suchen…" in body


def test_saved_locale_translates_home_gallery(client):
    client.post("/settings/locale", data={"locale": "de"})

    body = client.get("/").get_data(as_text=True)

    assert "<title>Startseite - Yaffo</title>" in body
    assert "Fotobibliothek" in body
    assert "Keine Fotos gefunden" in body
    assert "Passen Sie die Filter an oder versuchen Sie es später erneut." in body
    assert "Filter konfigurieren" in body
    assert "Auf Standardwerte zurücksetzen" in body


def test_saved_locale_translates_media_details(client, app):
    with app.app_context():
        media_item = MediaItem(full_file_path="/library/example.jpg")
        db.session.add(media_item)
        db.session.commit()
        media_item_id = media_item.id

    client.post("/settings/locale", data={"locale": "de"})
    body = client.get(f"/media/view/{media_item_id}").get_data(as_text=True)

    assert "Fotodetails" in body
    assert "Dateiinformationen" in body
    assert "Keine Ortsinformationen" in body
    assert "Personen (0)" in body
    assert "Gesichter (0)" in body
    assert "Keine Tags" in body
    assert "Änderungen speichern" in body


def test_saved_locale_translates_media_api_errors(client):
    client.post("/settings/locale", data={"locale": "de"})

    response = client.post("/api/media/999999/favorite")

    assert response.status_code == 404
    assert response.get_json() == {
        "error": "Foto nicht gefunden",
        "code": "photo_not_found",
    }


def test_saved_locale_translates_faces_inbox(client):
    client.post("/settings/locale", data={"locale": "de"})

    body = client.get("/faces").get_data(as_text=True)

    assert "<title>Gesichter - Yaffo</title>" in body
    assert "Nicht zugewiesene Gesichter" in body
    assert "Alle Gesichter zugewiesen!" in body
    assert "Gruppieren nach" in body
    assert "Ähnlichkeitsschwellenwert" in body
    assert "Auswahl zuweisen" in body
    assert "Auswahl ignorieren" in body
    assert "Empfohlener Ablauf" in body


@pytest.mark.parametrize(
    ("face_count", "expected_message"),
    [
        (0, "0 Gesichter wurden erfolgreich ignoriert"),
        (1, "1 Gesicht wurde erfolgreich ignoriert"),
        (2, "2 Gesichter wurden erfolgreich ignoriert"),
    ],
)
def test_saved_locale_translates_face_assignment_plural(client, app, face_count, expected_message):
    with app.app_context():
        faces = [
            Face(full_file_path=f"/faces/{index}.jpg", status=FACE_STATUS_UNASSIGNED)
            for index in range(face_count)
        ]
        db.session.add_all(faces)
        db.session.commit()
        face_ids = [face.id for face in faces]

    client.post("/settings/locale", data={"locale": "de"})
    response = client.post(
        "/api/faces/assign",
        json={"faces": face_ids, "person": None, "faceStatus": FACE_STATUS_IGNORED},
    )

    assert response.status_code == 200
    assert response.get_json() == {
        "success": True,
        "message": expected_message,
        "code": "faces_ignored",
        "face_ids": face_ids,
    }


def test_saved_locale_translates_face_assignment_validation_error(client):
    client.post("/settings/locale", data={"locale": "de"})

    response = client.post("/api/faces/assign", json={})

    assert response.status_code == 400
    assert response.get_json() == {
        "success": False,
        "message": "Gesichter, Person und Gesichtsstatus sind erforderlich",
        "code": "assignment_fields_required",
    }


def test_settings_locale_rejects_unsupported_locale(client):
    response = client.post("/settings/locale", data={"locale": "fr"})

    assert response.status_code == 400
    assert response.get_json() == {"error": "Unsupported locale"}
