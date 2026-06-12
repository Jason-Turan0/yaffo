"""Theme selection: persisted in ApplicationSettings, stamped on <html data-theme>."""
from yaffo import themes


def test_default_theme_on_html_element(client):
    response = client.get("/settings")
    assert response.status_code == 200
    assert b'<html lang="en" data-theme="classic">' in response.data


def test_set_theme_persists_and_renders(client):
    response = client.post("/settings/theme", data={"theme": "neobrutalist"})
    assert response.status_code == 204
    assert response.headers["HX-Refresh"] == "true"

    page = client.get("/settings")
    assert b'<html lang="en" data-theme="neobrutalist">' in page.data


def test_set_theme_rejects_unknown_slug(client):
    response = client.post("/settings/theme", data={"theme": "vaporwave"})
    assert response.status_code == 400
    assert response.get_json() == {"error": "Unknown theme: vaporwave"}

    page = client.get("/settings")
    assert b'data-theme="classic"' in page.data


def test_settings_page_lists_all_themes(client):
    page = client.get("/settings").data.decode()
    for slug, label in themes.THEMES.items():
        assert f'value="{slug}"' in page
        assert label in page