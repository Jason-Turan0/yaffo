from flask import Flask, Response, abort, render_template, request, send_from_directory

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


def _assemble_theme_css(assets) -> str:
    """A theme's full stylesheet: its token block followed by its skin."""
    return f"{assets.tokens_css}\n\n{assets.skin_css}".strip() + "\n"


def init_base_routes(app: Flask):
    @app.errorhandler(404)
    def page_not_found(error):
        return render_template('404.html'), 404

    @app.errorhandler(500)
    def internal_server_error(error):
        return render_template('500.html'), 500

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