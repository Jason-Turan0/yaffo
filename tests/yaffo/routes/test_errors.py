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


def test_saved_locale_translates_the_404(client):
    client.post("/settings/locale", data={"locale": "de"})

    body = client.get("/this/route/does/not/exist").data.decode()

    assert 'lang="de"' in body
    assert "Diese Seite wurde nicht entwickelt" in body
    assert "Dunkelkammer geschafft" in body
    assert "This page didn’t develop" not in body


def test_saved_locale_translates_the_500(app, client):
    @app.route("/_boom_500_de")
    def boom_de():
        from flask import abort
        abort(500)

    client.post("/settings/locale", data={"locale": "de"})
    body = client.get("/_boom_500_de").data.decode()

    assert 'lang="de"' in body
    assert "In der Dunkelkammer ist etwas schiefgelaufen" in body
    assert "Something went wrong in the darkroom" not in body


def test_error_screen_uses_inline_tokenized_icon(client):
    body = client.get("/nope").data.decode()

    # the icon is inlined (no <img>) so its fills come from theme tokens via CSS
    assert '<svg class="error-icon"' in body
    assert "error-icon-window" in body
    assert "error-icon.svg" not in body


def test_escaped_exception_renders_the_error_page_not_a_traceback(app):
    """The app under test has TESTING=True, so Flask would propagate an escaped
    exception straight out of the request — the same thing it does in the debug dev
    server, where the user got a Werkzeug traceback instead of a page."""
    @app.route("/_boom_unhandled")
    def boom():
        raise RuntimeError("something broke")

    response = app.test_client().get("/_boom_unhandled")

    assert response.status_code == 500
    body = response.data.decode()
    assert "Something went wrong in the darkroom" in body
    assert "Traceback" not in body
    assert "RuntimeError" not in body  # the detail belongs in the log, not the browser


def test_schema_mismatch_says_the_database_is_out_of_date(app):
    """A pending migration surfaces as a bare OperationalError ("no such column"),
    which the generic 500 copy ("give it another moment") reads as a transient blip.
    It isn't — restarting is what fixes it, so say so."""
    from sqlalchemy.exc import OperationalError

    @app.route("/_boom_schema")
    def boom_schema():
        raise OperationalError("SELECT media_items.orientation FROM media_items", {},
                               Exception("no such column: media_items.orientation"))

    response = app.test_client().get("/_boom_schema")

    assert response.status_code == 500
    body = response.data.decode()
    assert "Your library database is out of date" in body
    assert "Something went wrong in the darkroom" not in body


def test_api_route_gets_json_not_an_html_page(app):
    """The caller is JS expecting JSON; an HTML body would turn the error into a
    parse error at the fetch site."""
    from sqlalchemy.exc import OperationalError

    @app.route("/api/_boom_schema")
    def boom_api():
        raise OperationalError("SELECT x", {}, Exception("no such column: media_items.orientation"))

    response = app.test_client().get("/api/_boom_schema")

    assert response.status_code == 500
    assert response.is_json
    assert response.get_json()["code"] == "database_out_of_date"


def test_abort_404_still_reaches_the_404_handler(app):
    """The catch-all must not swallow HTTPExceptions."""
    @app.route("/_abort_404")
    def abort_404():
        from flask import abort
        abort(404)

    response = app.test_client().get("/_abort_404")

    assert response.status_code == 404
    assert "This page didn’t develop" in response.data.decode()


def test_failed_startup_serves_the_error_page_for_every_route(tmp_path):
    """Migrations that fail at boot used to kill the process with a traceback and no
    UI. Recovery mode serves the error screen instead — on every route, so the user
    can't click into a half-working app backed by a database it can't trust."""
    from sqlalchemy.exc import OperationalError

    from yaffo.app import create_app
    from yaffo.db import db

    boot_failure = OperationalError("stmt", {}, Exception("no such column: media_items.orientation"))
    broken = create_app(db_path=tmp_path / "test.db", config={"TESTING": True},
                        startup_error=boot_failure)
    # A real half-migrated database still has its settings table, so the themed page
    # renders; only the newest column is missing.
    with broken.app_context():
        db.create_all()

    response = broken.test_client().get("/")

    assert response.status_code == 500
    assert "Your library database is out of date" in response.data.decode()


def test_unreadable_database_still_gets_a_page_not_a_traceback(tmp_path):
    """The error templates read the theme and locale from the database — so when the
    database is the thing that's broken, rendering one can fail too. The handler must
    still answer with a page rather than raising out of itself."""
    from sqlalchemy.exc import OperationalError

    from yaffo.app import create_app

    # No create_all(): not one table exists, so even the theme lookup fails.
    boot_failure = OperationalError("stmt", {}, Exception("no such table: media_items"))
    broken = create_app(db_path=tmp_path / "empty.db", config={"TESTING": True},
                        startup_error=boot_failure)

    response = broken.test_client().get("/")

    assert response.status_code == 500
    body = response.data.decode()
    assert "Something went wrong" in body      # the self-contained fallback page
    assert "Traceback" not in body
