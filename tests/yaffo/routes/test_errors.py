"""Global error screens. The 404 handler renders an on-brand page that extends
base.html, so it carries the active theme and navigation like any other page."""


def test_unknown_url_returns_branded_404(client):
    response = client.get("/this/route/does/not/exist")

    assert response.status_code == 404
    body = response.data.decode()
    assert "This page didn’t develop" in body
    # extends base.html: keeps the themed shell and a way back home
    assert 'data-theme="classic"' in body
    assert 'href="/"' in body


def test_404_links_the_page_stylesheet(client):
    response = client.get("/nope")

    assert response.status_code == 404
    assert "error.css" in response.data.decode()


def test_viewing_missing_photo_renders_branded_404(client):
    response = client.get("/media/view/999999")

    assert response.status_code == 404
    # routed through the global handler, not the old plain-text "Photo not found"
    assert "This page didn’t develop" in response.data.decode()


def test_500_renders_branded_error(app):
    @app.route("/_boom_500")
    def boom():
        from flask import abort
        abort(500)

    response = app.test_client().get("/_boom_500")

    assert response.status_code == 500
    body = response.data.decode()
    assert "Something went wrong in the darkroom" in body
    assert "error.css" in body
    assert 'data-theme="classic"' in body


def test_error_screen_uses_inline_tokenized_icon(client):
    body = client.get("/nope").data.decode()

    # the icon is inlined (no <img>) so its fills come from theme tokens via CSS
    assert '<svg class="error-icon"' in body
    assert "error-icon-window" in body
    assert "error-icon.svg" not in body
