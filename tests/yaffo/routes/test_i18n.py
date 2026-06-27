import json
from datetime import date

import pytest
from flask_babel import gettext

from yaffo.db import db
from yaffo.db.models import (
    FACE_STATUS_IGNORED,
    FACE_STATUS_UNASSIGNED,
    ApplicationSettings,
    ClassificationLabel,
    Face,
    MediaItem,
    Person,
    PersonFace,
)
from yaffo.distance_units import DISTANCE_UNIT_SETTING
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


def test_missing_gettext_logs_warning(app, monkeypatch):
    warnings = []
    monkeypatch.setattr("yaffo.i18n.logger.warning", lambda *args: warnings.append(args))
    with app.app_context():
        db.session.add(ApplicationSettings(name=LOCALE_SETTING, type="string", value="de"))
        db.session.commit()
        with app.test_request_context("/"):
            assert gettext("Deliberately missing test translation") == "Deliberately missing test translation"

    assert warnings == [
        (
            "Missing translation for locale=%s key=%r",
            "de",
            "Deliberately missing test translation",
        )
    ]


def test_settings_distance_unit_persists(client, app):
    response = client.post("/settings/distance-unit", data={"distance_unit": "km"})

    assert response.status_code == 302
    with app.app_context():
        assert db.session.query(ApplicationSettings).filter_by(name=DISTANCE_UNIT_SETTING).one().value == "km"

    body = client.get("/settings").get_data(as_text=True)
    assert "<h2>Units</h2>" in body
    assert 'value="km" selected' in body


def test_saved_locale_translates_settings_media_directory_controls(client):
    client.post("/settings/locale", data={"locale": "de"})

    body = client.get("/settings").get_data(as_text=True)

    assert "<title>Einstellungen - Yaffo</title>" in body
    assert "Anwendungseinstellungen konfigurieren" in body
    assert "Medienverzeichnisse" in body
    assert "Keine Medienverzeichnisse konfiguriert" in body
    assert "Miniaturansichtsverzeichnis" in body
    assert "Kein Miniaturansichtsverzeichnis konfiguriert" in body
    assert "Einheiten" in body
    assert "Bevorzugte Entfernungseinheit" in body
    assert "Systeminformationen" in body
    assert 'data-action="add-media-dir"' in body
    assert 'data-action="change-thumbnail-dir"' in body
    assert "onclick=" not in body


def test_saved_locale_translates_settings_label_management(app, client):
    with app.app_context():
        db.session.add(ClassificationLabel(name="Hund", is_default=True))
        db.session.commit()

    client.post("/settings/locale", data={"locale": "de"})

    body = client.get("/settings").get_data(as_text=True)

    assert "Fotolabels" in body
    assert "Labels filtern…" in body
    assert "Labelname (zum Beispiel Hund)" in body
    assert "Label hinzufügen" in body
    assert "Alle Fotos neu klassifizieren" in body


def test_saved_locale_translates_settings_label_validation(client):
    client.post("/settings/locale", data={"locale": "de"})

    response = client.post(
        "/settings/labels",
        data={"action": "create", "name": "  "},
    )
    notification = json.loads(response.headers["HX-Trigger"])["showNotification"]

    assert response.status_code == 204
    assert notification == {
        "message": "Ein Labelname ist erforderlich.",
        "type": "error",
    }


def test_saved_locale_translates_settings_llm_forms(client, monkeypatch):
    monkeypatch.setattr(
        "yaffo.site_agents.llm_config.get_api_key",
        lambda provider_id: None,
    )
    client.post("/settings/locale", data={"locale": "de"})

    body = client.get("/settings").get_data(as_text=True)

    assert "KI-Generierung" in body
    assert "KI-gestützte Anpassungsfunktionen" in body
    assert ">Modell</label>" in body
    assert "Claude Sonnet 4.6 — ausgewogen" in body
    assert "Anthropic-API-Schlüssel:" in body
    assert "Nicht festgelegt" in body
    assert "Schlüssel festlegen" in body
    assert 'placeholder="API-Schlüssel"' in body


def test_saved_locale_translates_settings_llm_model_notification(client):
    client.post("/settings/locale", data={"locale": "de"})

    response = client.post(
        "/settings/llm/model",
        data={"model": "claude-opus-4-8"},
    )
    notification = json.loads(response.headers["HX-Trigger"])["showNotification"]

    assert notification == {
        "message": "KI-Modell wurde aktualisiert.",
        "type": "success",
    }


def test_saved_locale_translates_settings_media_directory_api_errors(client):
    client.post("/settings/locale", data={"locale": "de"})

    add_response = client.post("/api/settings/media-dirs", json={})
    remove_response = client.delete("/api/settings/media-dirs/999")
    thumbnail_response = client.post("/api/settings/thumbnail-dir", json={})

    assert add_response.status_code == 400
    assert add_response.get_json() == {
        "error": "Verzeichnispfad ist erforderlich",
        "code": "directory_path_required",
    }
    assert remove_response.status_code == 404
    assert remove_response.get_json() == {
        "error": "Medienverzeichnis nicht gefunden",
        "code": "media_directory_not_found",
    }
    assert thumbnail_response.status_code == 400
    assert thumbnail_response.get_json() == {
        "error": "Verzeichnispfad ist erforderlich",
        "code": "directory_path_required",
    }


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
    assert "intl-date-input-control" in body
    assert 'type="date"' not in body


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
    response = client.post("/settings/locale", data={"locale": "pt"})

    assert response.status_code == 400
    assert response.get_json() == {"error": "Unsupported locale"}


def test_arabic_locale_renders_rtl_document(client):
    response = client.post("/settings/locale", data={"locale": "ar"})

    assert response.status_code == 302
    body = client.get("/settings").get_data(as_text=True)
    assert '<html lang="ar" dir="rtl"' in body
