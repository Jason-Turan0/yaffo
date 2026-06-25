import pytest

from yaffo.db import db
from yaffo.db.models import ApplicationSettings, MediaItem
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


def test_settings_locale_rejects_unsupported_locale(client):
    response = client.post("/settings/locale", data={"locale": "fr"})

    assert response.status_code == 400
    assert response.get_json() == {"error": "Unsupported locale"}
