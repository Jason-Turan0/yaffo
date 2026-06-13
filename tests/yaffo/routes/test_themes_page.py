"""Themes page: lists system + custom themes, sets the default (persisted in
ApplicationSettings, stamped on <html data-theme>), creates and deletes custom
themes."""
from yaffo import themes


def test_default_theme_on_html_element(client):
    response = client.get("/themes", follow_redirects=True)
    assert response.status_code == 200
    assert b'<html lang="en" data-theme="classic">' in response.data


def test_index_redirects_to_default_theme_panel(client):
    response = client.get("/themes")
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/themes/classic")


def test_set_default_persists_and_renders(client):
    response = client.post("/themes/neobrutalist/default")
    assert response.status_code == 204
    assert response.headers["HX-Refresh"] == "true"

    page = client.get("/themes/neobrutalist")
    assert b'<html lang="en" data-theme="neobrutalist">' in page.data


def test_set_default_rejects_unknown_slug(client):
    response = client.post("/themes/vaporwave/default")
    assert response.status_code == 400
    assert response.get_json() == {"error": "Unknown theme: vaporwave"}

    page = client.get("/themes/classic")
    assert b'data-theme="classic"' in page.data


def test_show_unknown_theme_404s(client):
    assert client.get("/themes/vaporwave").status_code == 404


def test_page_lists_system_and_custom_themes(client):
    page = client.get("/themes/classic").data.decode()
    for slug, label in themes.THEMES.items():
        assert f'/themes/{slug}"' in page
        assert label in page
    for theme in themes.list_custom_themes():
        assert f'/themes/{theme.slug}"' in page
        assert theme.label in page


def test_create_custom_theme(client):
    response = client.post("/themes/create", data={"label": "Vapor Wave"})
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/themes/vapor-wave")

    created = themes.get_custom_theme("vapor-wave")
    assert created.label == "Vapor Wave"
    assert '[data-theme="vapor-wave"]' in created.tokens_css

    panel = client.get("/themes/vapor-wave").data.decode()
    assert "Vapor Wave" in panel
    assert "Custom" in panel


def test_create_duplicate_label_gets_unique_slug(client):
    client.post("/themes/create", data={"label": "Vapor Wave"})
    response = client.post("/themes/create", data={"label": "Vapor Wave"})
    assert response.headers["Location"].endswith("/themes/vapor-wave-2")
    assert themes.get_custom_theme("vapor-wave-2") is not None


def test_create_requires_label(client):
    response = client.post("/themes/create", data={"label": "  "})
    assert response.status_code == 400


def test_delete_custom_theme(client):
    client.post("/themes/create", data={"label": "Vapor Wave"})
    response = client.post("/themes/vapor-wave/delete")
    assert response.status_code == 302
    assert themes.get_custom_theme("vapor-wave") is None


def test_delete_active_custom_theme_falls_back_to_default(client):
    client.post("/themes/create", data={"label": "Vapor Wave"})
    client.post("/themes/vapor-wave/default")
    client.post("/themes/vapor-wave/delete")
    assert themes.get_theme() == themes.DEFAULT_THEME


def test_delete_system_theme_rejected(client):
    response = client.post("/themes/classic/delete")
    assert response.status_code == 400
    assert themes.theme_exists("classic")


def test_settings_page_no_longer_has_theme_control(client):
    page = client.get("/settings").data.decode()
    assert "theme-select" not in page
    assert "Appearance" not in page