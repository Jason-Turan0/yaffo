from pathlib import Path

from flask import Flask, Response, abort, jsonify, render_template, request, send_from_directory
from flask_babel import gettext
from sqlalchemy.exc import OperationalError
from werkzeug.exceptions import HTTPException

from yaffo import themes
from yaffo.db import db
from yaffo.db.repositories import media_dir_repository
from yaffo.logging_config import get_logger
from yaffo.utils.file_system import DirEntry, list_directory, listing_to_dict

logger = get_logger(__name__, 'webapp')

# Last resort, used only when even the error template can't be rendered (the theme and
# locale it needs are read from the database). Self-contained on purpose: no template,
# no stylesheet, no database.
FALLBACK_ERROR_PAGE = """<!doctype html>
<meta charset="utf-8">
<title>Yaffo — something went wrong</title>
<style>
  body { font-family: system-ui, sans-serif; margin: 0; display: grid; place-items: center;
         min-height: 100vh; background: #f8f9fa; color: #212529; }
  main { max-width: 32rem; padding: 2rem; text-align: center; }
  code { background: #e9ecef; padding: 0.15em 0.4em; border-radius: 3px; }
</style>
<main>
  <h1>Something went wrong</h1>
  <p>Yaffo could not load this page, and could not load its own error page either —
     the library database may be unreadable or out of date.</p>
  <p>Restarting Yaffo applies any pending database updates. If that doesn't help,
     the details are in the log.</p>
</main>
"""


def _css_response(css: str) -> Response:
    """Assembled (or DB-backed) CSS: don't let the browser cache stale content."""
    response = Response(css, mimetype="text/css")
    response.headers["Cache-Control"] = "no-store"
    return response


def _db_response(content: str, mimetype: str) -> Response:
    response = Response(content, mimetype=mimetype)
    response.headers["Cache-Control"] = "no-store"
    return response


def _assemble_theme_css(assets) -> str:
    """A theme's full stylesheet: its token block followed by its skin."""
    return f"{assets.tokens_css}\n\n{assets.skin_css}".strip() + "\n"


def _shortcut_key(path_value: str) -> str:
    try:
        return str(Path(path_value).expanduser().resolve())
    except OSError:
        return str(Path(path_value).expanduser())


def _append_unique_roots(roots: list[DirEntry], extra_roots: list[DirEntry]) -> None:
    seen = {_shortcut_key(root.path) for root in roots}
    for root in extra_roots:
        key = _shortcut_key(root.path)
        if key in seen:
            continue
        seen.add(key)
        roots.append(root)


def _configured_media_dir_roots() -> list[DirEntry]:
    roots: list[DirEntry] = []
    for media_dir in media_dir_repository.get_media_dir_entries(db.session):
        directory = media_dir.path.expanduser()
        if not directory.is_dir():
            continue
        roots.append(DirEntry(name=directory.name or str(directory), path=str(directory), is_dir=True))
    return roots


def is_schema_mismatch(error: BaseException) -> bool:
    """Does this look like the database not matching the code that's running?

    SQLite reports a missing migration as a plain OperationalError ("no such column:
    media_items.orientation"), which is indistinguishable from any other query error
    by type alone — hence the message sniff.
    """
    if not isinstance(error, OperationalError):
        return False
    message = str(getattr(error, "orig", error)).lower()
    return "no such column" in message or "no such table" in message


def render_critical_error(error: BaseException):
    """The branded error screen for a failure that took the whole request down.

    Falls back to a self-contained page if rendering itself fails: the templates pull
    the theme and locale from the database, which is exactly what may be broken here,
    and a handler that raises would hand the user a traceback — the thing it exists
    to prevent.
    """
    schema_mismatch = is_schema_mismatch(error)
    template = 'db_error.html' if schema_mismatch else '500.html'
    try:
        return render_template(template), 500
    except Exception:  # noqa: BLE001 - the database is too broken to render a page from
        logger.exception("could not render the error page")
        return Response(FALLBACK_ERROR_PAGE, mimetype="text/html", status=500)


def init_base_routes(app: Flask):
    @app.errorhandler(404)
    def page_not_found(error):
        return render_template('404.html'), 404

    @app.errorhandler(500)
    def internal_server_error(error):
        return render_template('500.html'), 500

    @app.errorhandler(Exception)
    def unhandled_exception(error):
        """Anything a route let escape — a schema mismatch, a bad query, a bug.

        Without this, Flask only reaches the 500 page in production: in debug (the
        `flask run` dev flow) it propagates instead, and the browser gets a Werkzeug
        traceback. The traceback still goes to the log, where it belongs; the browser
        gets a page. HTTPExceptions are re-raised untouched so abort(404) and friends
        keep their own handlers.
        """
        if isinstance(error, HTTPException):
            return error

        logger.exception("unhandled error serving %s %s", request.method, request.path)

        # An /api caller is JS expecting JSON; handing it an HTML page just turns one
        # error into a parse error. Mirrors the {success, message, code} shape the
        # other API routes use.
        if request.path.startswith('/api/'):
            schema_mismatch = is_schema_mismatch(error)
            return jsonify({
                "success": False,
                "message": (gettext("The library database is out of date. Restart Yaffo to update it.")
                            if schema_mismatch else gettext("Something went wrong")),
                "code": "database_out_of_date" if schema_mismatch else "internal_error",
            }), 500

        return render_critical_error(error)

    @app.route('/api/fs/list', methods=["GET"])
    def fs_list():
        """Browse the local filesystem for the in-app folder/file picker. `path` is the
        directory to list (defaults to home); `mode` is "folder", "file", or "any"."""
        listing = list_directory(request.args.get('path'), request.args.get('mode', 'folder'))
        _append_unique_roots(listing.roots, _configured_media_dir_roots())
        return jsonify(listing_to_dict(listing))

    @app.route('/api/fs/create-folder', methods=["POST"])
    def fs_create_folder():
        """Create one subfolder under the picker’s current directory."""
        data = request.get_json(silent=True) or {}
        parent_value = str(data.get("path") or "").strip()
        name = str(data.get("name") or "").strip()
        if not name:
            return jsonify({"error": gettext("Folder name is required")}), 400
        if name in (".", "..") or "/" in name or "\\" in name:
            return jsonify({"error": gettext("Folder name cannot contain path separators")}), 400
        parent = Path(parent_value).expanduser()
        if not parent.is_dir():
            return jsonify({"error": gettext("Choose an existing parent folder")}), 400
        child = parent / name
        if child.exists():
            return jsonify({"error": gettext("A file or folder with that name already exists")}), 400
        try:
            child.mkdir()
        except OSError as exc:
            return jsonify({"error": gettext("Could not create folder: %(reason)s", reason=str(exc))}), 400
        return jsonify({"path": str(child)}), 201

    @app.route('/favicon.ico', methods=["GET"])
    def favicon():
        theme = request.args.get('theme', themes.DEFAULT_THEME)
        if not themes.is_builtin(theme):
            custom = themes.get_custom_theme(theme)
            if custom and custom.published_theme.favicon_svg:
                return _db_response(custom.published_theme.favicon_svg, "image/svg+xml")
            theme = themes.DEFAULT_THEME
        return send_from_directory(f'static/themes/{theme}', 'favicon.svg')

    @app.route('/themes/<slug>/theme.css', methods=["GET"])
    def theme_css(slug: str):
        """The active theme's full stylesheet (token block + skin), linked once
        from base.html. Built-in themes assemble it from their static files;
        custom themes serve it from the DB. Either way the shape is the same."""
        assets = themes.read_theme_css(slug)
        if assets is None:
            abort(404)
        return _css_response(_assemble_theme_css(assets))

    @app.route('/themes/<slug>/preview.css', methods=["GET"])
    def theme_preview_css(slug: str):
        """Like theme.css, but a custom theme with an unpublished working draft serves
        that draft — so the themes page can preview a generation before it's saved.
        Built-in themes and themes without a draft fall back to the live CSS."""
        assets = None
        if not themes.is_builtin(slug):
            custom = themes.get_custom_theme(slug)
            if custom is None:
                abort(404)
            assets = custom.working_theme  # the draft, when one is pending
        if assets is None:
            assets = themes.read_theme_css(slug)
        if assets is None:
            abort(404)
        return _css_response(_assemble_theme_css(assets))

    @app.route('/themes/<slug>/tokens.css', methods=["GET"])
    def theme_tokens_css(slug: str):
        """Token-override block only, for sandboxed widget frames: skin rules
        target body/app classes and would leak into widget documents. The
        default theme serves an empty sheet — its tokens are static/tokens.css's
        :root block, which frames already link."""
        assets = themes.read_theme_css(slug)
        if assets is None:
            abort(404)
        if not assets.tokens_css:
            return _css_response("/* default theme: tokens are static/tokens.css's :root block */\n")
        return _css_response(assets.tokens_css + "\n")
