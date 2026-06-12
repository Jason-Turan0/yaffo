from pathlib import Path

from flask import Flask, Response, abort, request, send_from_directory

from yaffo import themes


def _css_response(css: str) -> Response:
    """Assembled (or DB-backed) CSS: don't let the browser cache stale content."""
    response = Response(css, mimetype="text/css")
    response.headers["Cache-Control"] = "no-store"
    return response


def _db_response(content: str, mimetype: str) -> Response:
    response = Response(content, mimetype=mimetype)
    response.headers["Cache-Control"] = "no-store"
    return response


def init_base_routes(app: Flask):
    themes_dir = Path(app.static_folder) / "themes"

    def _builtin_theme_css(slug: str) -> str:
        """tokens (themes/<slug>/tokens.css; the default theme's live in
        static/tokens.css instead) + skin (themes/<slug>.css)."""
        parts = []
        tokens = themes_dir / slug / "tokens.css"
        if tokens.is_file():
            parts.append(tokens.read_text())
        skin = themes_dir / f"{slug}.css"
        if skin.is_file():
            parts.append(skin.read_text())
        return "\n\n".join(parts)

    @app.route('/favicon.ico', methods=["GET"])
    def favicon():
        theme = request.args.get('theme', themes.DEFAULT_THEME)
        if not themes.is_builtin(theme):
            custom = themes.get_custom_theme(theme)
            if custom and custom.favicon_svg:
                return _db_response(custom.favicon_svg, "image/svg+xml")
            theme = themes.DEFAULT_THEME
        return send_from_directory(f'static/themes/{theme}', 'favicon.svg')

    @app.route('/themes/<slug>/theme.css', methods=["GET"])
    def theme_css(slug: str):
        """The active theme's full stylesheet (token block + skin), linked once
        from base.html. Built-in themes assemble it from their static files;
        custom themes serve it from the DB. Either way the shape is the same."""
        if themes.is_builtin(slug):
            return _css_response(_builtin_theme_css(slug))
        custom = themes.get_custom_theme(slug)
        if custom is None:
            abort(404)
        return _css_response(f"{custom.tokens_css}\n\n{custom.skin_css}".strip() + "\n")

    @app.route('/themes/<slug>/tokens.css', methods=["GET"])
    def theme_tokens_css(slug: str):
        """Token-override block only, for sandboxed widget frames: skin rules
        target body/app classes and would leak into widget documents. The
        default theme serves an empty sheet — its tokens are static/tokens.css's
        :root block, which frames already link."""
        if themes.is_builtin(slug):
            tokens = themes_dir / slug / "tokens.css"
            if not tokens.is_file():
                return _css_response("/* default theme: tokens are static/tokens.css's :root block */\n")
            return _css_response(tokens.read_text())
        custom = themes.get_custom_theme(slug)
        if custom is None:
            abort(404)
        return _css_response(custom.tokens_css + "\n")