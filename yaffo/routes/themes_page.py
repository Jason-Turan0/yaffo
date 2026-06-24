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
from yaffo.background_tasks.tasks.generate_theme import generate_theme_task
from yaffo.db.models import (
    CONVERSATION_TYPE_USER,
    PAGE_VERSION_STATUS_ACCEPTED,
    PAGE_VERSION_STATUS_CANCELLED,
    PAGE_VERSION_STATUS_IN_PROGRESS,
    PAGE_VERSION_STATUS_READY,
)
from yaffo.site_agents import llm_config
from yaffo.themes import CustomTheme, ThemeAssets

# save_custom_theme caps slugs at 41 chars; leave room for a "-N" uniqueness
# suffix when two labels slugify identically.
_MAX_BASE_SLUG_LENGTH = 30


def _slugify(label: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")
    if not re.match(r"^[a-z]", slug):
        slug = f"custom-{slug}" if slug else "custom"
    return slug[:_MAX_BASE_SLUG_LENGTH].rstrip("-")


def _unique_slug(label: str, exclude: str | None = None) -> str:
    base = _slugify(label)
    slug = base
    counter = 2
    while themes.theme_exists(slug) and slug != exclude:
        slug = f"{base}-{counter}"
        counter += 1
    return slug


def init_themes_page_routes(app: Flask):
    def _render_page(selected_slug: str):
        # Built-in themes are hand-crafted and have no conversation; custom themes
        # carry the transcript that designed them (and the in-flight status that
        # tells the chat whether a generation is still running).
        selected_theme = (
            None
            if themes.is_builtin(selected_slug)
            else themes.get_custom_theme(selected_slug)
        )
        return render_template(
            "themes_page/index.html",
            # Render the page AS the theme being viewed (overriding the active default),
            # previewing its working draft when one is pending — so the look is visible
            # before it's made default or published.
            theme=selected_slug,
            theme_css_url=url_for("theme_preview_css", slug=selected_slug),
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
            selected_conversations=selected_theme.conversations if selected_theme else [],
            selected_status=selected_theme.status if selected_theme else None,
            # A finished generation leaves a working draft (READY) awaiting Save; the
            # panel shows the publish/discard controls only then.
            selected_has_draft=(
                selected_theme is not None
                and selected_theme.status == PAGE_VERSION_STATUS_READY
                and selected_theme.working_theme is not None
            ),
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

    def _hx_refresh():
        # Theme CSS lives on <html data-theme> / the linked stylesheet, outside any
        # swappable fragment, so ask htmx for a full page refresh.
        response = make_response("", 204)
        response.headers["HX-Refresh"] = "true"
        return response

    @app.route("/themes/<slug>/default", methods=["POST"])
    def themes_set_default(slug: str):
        if not themes.theme_exists(slug):
            return jsonify({"error": f"Unknown theme: {slug}"}), 400
        themes.set_theme(slug)
        return _hx_refresh()

    @app.route("/themes/<slug>/publish", methods=["POST"])
    def themes_publish(slug: str):
        """Save a finished generation: promote the working draft into the published
        theme the app serves. Enabled only when a READY draft exists."""
        theme = themes.get_custom_theme(slug)
        if theme is None:
            abort(404)
        if theme.status != PAGE_VERSION_STATUS_READY or theme.working_theme is None:
            return jsonify({"error": "No theme draft to publish."}), 409
        themes.publish_theme(slug)
        return _hx_refresh()

    @app.route("/themes/<slug>/discard", methods=["POST"])
    def themes_discard(slug: str):
        """Drop a finished generation's draft and keep the published theme as-is."""
        theme = themes.get_custom_theme(slug)
        if theme is None:
            abort(404)
        themes.discard_theme_draft(slug)
        return _hx_refresh()

    @app.route("/themes/create", methods=["POST"])
    def themes_create():
        label = (request.form.get("label") or "").strip()
        if not label:
            return jsonify({"error": "Theme name is required"}), 400
        slug = _unique_slug(label)
        # A new theme starts published as an empty token override (the classic
        # look) with no conversation yet; the chat with the generator agent fills
        # in a working_theme that is later published over this one.
        themes.save_custom_theme(
            CustomTheme(
                slug=slug,
                label=label,
                status=PAGE_VERSION_STATUS_ACCEPTED,
                conversations=[],
                published_theme=ThemeAssets(tokens_css=f'[data-theme="{slug}"] {{\n}}\n'),
                working_theme=None,
            )
        )
        return redirect(url_for("themes_show", slug=slug))

    @app.route("/themes/<slug>/rename", methods=["POST"])
    def themes_rename(slug: str):
        """Rename a custom theme. The slug is re-derived from the new label and the
        theme moves to it, so the response redirects to the new URL."""
        if themes.is_builtin(slug):
            return jsonify({"error": "System themes cannot be renamed"}), 400
        if themes.get_custom_theme(slug) is None:
            abort(404)
        label = (request.form.get("label") or "").strip()
        if not label:
            return jsonify({"error": "Theme name is required"}), 400
        new_slug = _unique_slug(label, exclude=slug)
        themes.rename_custom_theme(slug, new_slug, label)
        return redirect(url_for("themes_show", slug=new_slug))

    @app.route("/themes/<slug>/delete", methods=["POST"])
    def themes_delete(slug: str):
        if themes.is_builtin(slug):
            return jsonify({"error": "System themes cannot be deleted"}), 400
        themes.delete_custom_theme(slug)
        return redirect(url_for("themes_index"))

    def _theme_status_payload(theme: CustomTheme) -> dict:
        """Poll contract for an in-flight theme generation: the status the client
        gates Send/Cancel on, the timestamp that drives the elapsed counter (the last
        prompt that kicked off the run), and the conversation feed."""
        started_at = next(
            (entry.created_at for entry in reversed(theme.conversations)
             if entry.type == CONVERSATION_TYPE_USER),
            None,
        )
        return {
            "slug": theme.slug,
            "status": theme.status,
            "started_at": started_at,
            "messages": [{"type": entry.type, "content": entry.content} for entry in theme.conversations],
        }

    @app.route("/themes/<slug>/chat", methods=["POST"])
    def themes_chat(slug: str):
        """Start or continue an async theme generation, returning at once. The run
        lives on the theme (status + transcript), not this request, so it survives tab
        close / reload. A generation already running is rejected."""
        if themes.is_builtin(slug):
            return {"error": "System themes cannot be generated."}, 400
        theme = themes.get_custom_theme(slug)
        if theme is None:
            abort(404)
        if theme.status == PAGE_VERSION_STATUS_IN_PROGRESS:
            return {"error": "A generation is already running."}, 409
        payload = request.get_json(silent=True) or {}
        message = (payload.get("message") or "").strip()
        if not message:
            return {"error": "Message is required."}, 400
        if llm_config.selected_key_missing():
            return {"error": f"No API key configured. Add your {llm_config.selected_provider_label()} API key in Settings → AI Generation."}, 400

        themes.add_theme_message(slug, CONVERSATION_TYPE_USER, message)
        themes.set_theme_status(slug, PAGE_VERSION_STATUS_IN_PROGRESS)
        generate_theme_task(slug, message)
        return {"slug": slug}, 202

    @app.route("/themes/<slug>/status", methods=["GET"])
    def themes_status(slug: str):
        """Poll an in-flight generation: status, timing, and the conversation feed."""
        theme = themes.get_custom_theme(slug)
        if theme is None:
            abort(404)
        return _theme_status_payload(theme)

    @app.route("/themes/<slug>/cancel", methods=["POST"])
    def themes_cancel(slug: str):
        """Cancel a generation: flag CANCELLED (the running task polls this and stops).
        The published theme is untouched, so the UI snaps back to it on reload."""
        theme = themes.get_custom_theme(slug)
        if theme is None:
            abort(404)
        themes.set_theme_status(slug, PAGE_VERSION_STATUS_CANCELLED)
        return "", 204

