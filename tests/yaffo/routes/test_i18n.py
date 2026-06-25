from datetime import date

import pytest

from yaffo.db import db
from yaffo.db.models import (
    FACE_STATUS_IGNORED,
    FACE_STATUS_UNASSIGNED,
    ApplicationSettings,
    Face,
    MediaItem,
    Person,
    PersonFace,
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


def test_saved_locale_translates_empty_people_list(client):
    client.post("/settings/locale", data={"locale": "de"})

    body = client.get("/people").get_data(as_text=True)

    assert "<title>Personen - Yaffo</title>" in body
    assert "Noch keine Personen" in body
    assert "Fügen Sie Ihre erste Person hinzu, um Fotos zu organisieren." in body
    assert "Person hinzufügen" in body
    assert "Nicht angegeben" in body
    assert "Geburtsdatum" in body


def test_saved_locale_translates_people_table_and_formats_birthdate(client, app):
    with app.app_context():
        db.session.add(Person(name="Alex", gender=1, birthdate=date(1990, 1, 2)))
        db.session.commit()

    client.post("/settings/locale", data={"locale": "de"})
    body = client.get("/people").get_data(as_text=True)

    assert ">Geboren</th>" in body
    assert ">Gesichter</th>" in body
    assert ">Fotos</th>" in body
    assert ">Aktionen</th>" in body
    assert ">Männlich" in body
    assert "02.01.1990" in body
    assert ">Bearbeiten</a>" in body
    assert ">Löschen</a>" in body


def test_saved_locale_translates_people_api_error(client):
    client.post("/settings/locale", data={"locale": "de"})

    response = client.post("/api/people/create", json={"name": ""})

    assert response.status_code == 400
    assert response.get_json() == {
        "error": "Name ist erforderlich",
        "code": "name_required",
    }


def test_saved_locale_translates_people_flash(client):
    client.post("/settings/locale", data={"locale": "de"})

    body = client.post(
        "/people/create",
        data={"name": "Alex", "gender": ""},
        follow_redirects=True,
    ).get_data(as_text=True)

    assert "Alex wurde hinzugefügt" in body


def test_saved_locale_translates_empty_person_face_gallery(client, app):
    with app.app_context():
        person = Person(name="Alex")
        db.session.add(person)
        db.session.commit()
        person_id = person.id

    client.post("/settings/locale", data={"locale": "de"})
    body = client.get(f"/people/{person_id}/faces").get_data(as_text=True)

    assert "<title>Gesichter von Alex - Yaffo</title>" in body
    assert "Gesichter von Alex" in body
    assert "Zurück zu Personen" in body
    assert "Minimale Ähnlichkeit" in body
    assert "Maximale Ähnlichkeit" in body
    assert "Keine Gesichter gefunden" in body
    assert "Alex wurden noch keine Gesichter zugewiesen." in body
    assert "Auswahl entfernen" in body


def test_saved_locale_translates_person_face_gallery_details(client, app):
    with app.app_context():
        person = Person(name="Alex")
        media_item = MediaItem(full_file_path="/library/example.jpg", date_taken="2026-06-25T10:30:00")
        db.session.add_all([person, media_item])
        db.session.flush()
        face = Face(
            full_file_path="/faces/example.jpg",
            media_item_id=media_item.id,
            status="ASSIGNED",
        )
        db.session.add(face)
        db.session.flush()
        db.session.add(PersonFace(person_id=person.id, face_id=face.id, similarity=0.9))
        db.session.commit()
        person_id = person.id

    client.post("/settings/locale", data={"locale": "de"})
    body = client.get(f"/people/{person_id}/faces").get_data(as_text=True)

    assert "1 Gesicht wird angezeigt" in body
    assert 'alt="Gesicht"' in body
    assert "25.06.2026" in body
    assert "Ähnlichkeit:" in body
    assert "Alle auswählen" in body
    assert "Auswahl aufheben" in body


def test_saved_locale_translates_person_face_removal_validation(client, app):
    with app.app_context():
        person = Person(name="Alex")
        db.session.add(person)
        db.session.commit()
        person_id = person.id

    client.post("/settings/locale", data={"locale": "de"})
    body = client.post(
        f"/people/{person_id}/faces/remove",
        data={},
        headers={"Referer": f"/people/{person_id}/faces"},
        follow_redirects=True,
    ).get_data(as_text=True)

    assert "Keine Gesichter ausgewählt" in body


def test_saved_locale_translates_locations_page(client, app):
    with app.app_context():
        db.session.add(
            MediaItem(
                full_file_path="/library/example.jpg",
                latitude=52.52,
                longitude=13.405,
                location_name="Berlin",
            )
        )
        db.session.commit()

    client.post("/settings/locale", data={"locale": "de"})
    body = client.get("/locations").get_data(as_text=True)

    assert "<title>Orte - Yaffo</title>" in body
    assert "Fotoorte" in body
    assert "Nur Fotos ohne Ortsnamen anzeigen" in body
    assert "<kbd>Umschalt</kbd>" in body
    assert 'aria-label="Schließen"' in body
    assert '"name": "Berlin"' in body


def test_saved_locale_translates_locations_bulk_update_validation(client):
    client.post("/settings/locale", data={"locale": "de"})

    response = client.post("/locations/bulk-update", json={})

    assert response.status_code == 400
    assert response.get_json() == {
        "error": "Medienelement-IDs und Ortsname sind erforderlich",
        "code": "location_fields_required",
    }


def test_saved_locale_translates_reverse_geocode_validation(client):
    client.post("/settings/locale", data={"locale": "de"})

    response = client.post("/locations/reverse-geocode", json={})

    assert response.status_code == 400
    assert response.get_json() == {
        "error": "Breiten- und Längengrad sind erforderlich",
        "code": "coordinates_required",
    }


def test_settings_locale_rejects_unsupported_locale(client):
    response = client.post("/settings/locale", data={"locale": "fr"})

    assert response.status_code == 400
    assert response.get_json() == {"error": "Unsupported locale"}
