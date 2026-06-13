"""Theme management page.

Backed by the registry in yaffo.themes: built-in (system) themes ship with the
app and cannot be deleted; custom themes live in ApplicationSettings and will
eventually be designed through a conversation with an AI agent (the chat dialog
on each panel is the prepared UI for that). One theme is the default — the
active theme stamped on <html data-theme>.
"""
import re

from flask import Flask, abort, jsonify, make_response, redirect, render_template, request, url_for

from yaffo import themes
from yaffo.themes import CustomTheme

# save_custom_theme caps slugs at 41 chars; leave room for a "-N" uniqueness
# suffix when two labels slugify identically.
_MAX_BASE_SLUG_LENGTH = 30


def _slugify(label: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")
    if not re.match(r"^[a-z]", slug):
        slug = f"custom-{slug}" if slug else "custom"
    return slug[:_MAX_BASE_SLUG_LENGTH].rstrip("-")


def _unique_slug(label: str) -> str:
    base = _slugify(label)
    slug = base
    counter = 2
    while themes.theme_exists(slug):
        slug = f"{base}-{counter}"
        counter += 1
    return slug


def init_themes_page_routes(app: Flask):
    def _render_page(selected_slug: str):
        return render_template(
            "themes_page/index.html",
            system_themes=[
                {"slug": slug, "label": label} for slug, label in themes.THEMES.items()
            ],
            custom_themes=[
                {"slug": theme.slug, "label": theme.label}
                for theme in themes.list_custom_themes()
            ],
            selected_slug=selected_slug,
            selected_label=themes.list_themes()[selected_slug],
            selected_is_builtin=themes.is_builtin(selected_slug),
            default_slug=themes.get_theme(),
        )

    @app.route("/themes", methods=["GET"])
    def themes_index():
        return redirect(url_for("themes_show", slug=themes.get_theme()))

    @app.route("/themes/<slug>", methods=["GET"])
    def themes_show(slug: str):
        if not themes.theme_exists(slug):
            abort(404)
        return _render_page(slug)

    @app.route("/themes/<slug>/default", methods=["POST"])
    def themes_set_default(slug: str):
        if not themes.theme_exists(slug):
            return jsonify({"error": f"Unknown theme: {slug}"}), 400
        themes.set_theme(slug)
        # The theme lives on <html data-theme>, outside any swappable
        # fragment, so ask htmx for a full page refresh.
        response = make_response("", 204)
        response.headers["HX-Refresh"] = "true"
        return response

    @app.route("/themes/create", methods=["POST"])
    def themes_create():
        label = (request.form.get("label") or "").strip()
        if not label:
            return jsonify({"error": "Theme name is required"}), 400
        slug = _unique_slug(label)
        # A new theme starts as an empty token override (the classic look);
        # the conversation with the generator agent will fill it in.
        themes.save_custom_theme(
            CustomTheme(slug=slug, label=label, tokens_css=f'[data-theme="{slug}"] {{\n}}\n')
        )
        return redirect(url_for("themes_show", slug=slug))

    @app.route("/themes/<slug>/delete", methods=["POST"])
    def themes_delete(slug: str):
        if themes.is_builtin(slug):
            return jsonify({"error": "System themes cannot be deleted"}), 400
        themes.delete_custom_theme(slug)
        return redirect(url_for("themes_index"))