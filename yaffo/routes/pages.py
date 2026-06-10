from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    abort,
    make_response,
)

from yaffo.background_tasks.tasks import generate_page_task
from yaffo.db import db
from yaffo.db.models import (
    PAGE_VERSION_STATUS_CANCELLED,
    PAGE_VERSION_STATUS_IN_PROGRESS,
    PAGE_VERSION_STATUS_READY,
    Widget,
)
from yaffo.db.repositories import custom_page_repository as page_repo
from yaffo.db.repositories.data_query_repository import resolve_data_query, resolve_query
from yaffo.logging_config import get_logger
from yaffo.page_builder import llm_config
from yaffo.page_builder.serializers import version_status_payload
from yaffo.page_builder.widget_api import widget_api_source
from yaffo.utils.context import context

logger = get_logger(__name__)


# Sandboxed widget frames may only run their own inline code and load images
# from this app's origin (the photo routes); no other network (connect-src
# 'none') so injected data can't be exfiltrated.
def _widget_frame_csp(origin: str) -> str:
    return (
        f"default-src 'none'; img-src {origin} data:; style-src 'unsafe-inline'; "
        "script-src 'unsafe-inline'; connect-src 'none'"
    )


def _resolve_widget_data(data_query: dict) -> dict:
    """Resolve a widget's named queries to {query_name: rows}, failing closed: an
    empty query set, or one that fails validation, renders against nothing (the
    sandbox guarantee) rather than erroring the frame."""
    if not data_query:
        return {}
    try:
        return resolve_data_query(db.session, data_query)
    except ValueError:
        return {}


@context("yaffo-pages")
def init_pages_routes(app: Flask):
    @app.context_processor
    def inject_nav_pages():
        return {"nav_pages": page_repo.list_pages(db.session)}

    @app.context_processor
    def inject_widget_api():
        # The window.yaffo runtime, inlined into each widget frame (widget_frame.html).
        return {"widget_api_js": widget_api_source()}

    @app.route("/pages", methods=["POST"])
    def pages_create():
        title = (request.form.get("title") or "").strip() or "Untitled Page"
        page_subtitle = (request.form.get("page_subtitle") or "").strip()
        page = page_repo.create_page(db.session, title=title, subtitle=page_subtitle)
        return redirect(url_for("pages_detail", page_id=page.id))

    @app.route("/pages/<int:page_id>", methods=["GET"])
    def pages_detail(page_id: int):
        page = page_repo.get_page(db.session, page_id)
        if page is None:
            abort(404)
        if not page.widgets:
            return redirect(url_for("pages_design", page_id=page_id))
        return render_template("pages/presentation.html", page=page)

    @app.route("/pages/<int:page_id>/design", methods=["GET"])
    def pages_design(page_id: int):
        page = page_repo.get_page(db.session, page_id)
        if page is None:
            abort(404)
        return render_template("pages/design.html", page=page)

    @app.route("/pages/<int:page_id>/update", methods=["POST"])
    def pages_update(page_id: int):
        if page_repo.get_page(db.session, page_id) is None:
            abort(404)
        payload = request.get_json(silent=True) or {}
        title = (payload.get("title") or "").strip() or "Untitled Page"
        subtitle = (payload.get("subtitle") or "").strip()
        show_title = bool(payload.get("show_title", True))
        page_repo.update_page(db.session, page_id, title=title, subtitle=subtitle, show_title=show_title)
        page_repo.save_page_widgets(db.session, page_id, payload.get("widgets", []))
        return "", 204

    @app.route("/pages/<int:page_id>/delete", methods=["POST"])
    def pages_delete(page_id: int):
        page_repo.delete_page(db.session, page_id)
        return redirect(url_for("index"))

    @app.route("/pages/<int:page_id>/widgets/<widget_id>/frame", methods=["GET"])
    def pages_widget_frame(page_id: int, widget_id: str):
        widget = page_repo.get_widget(db.session, page_id, widget_id)
        if widget is None:
            abort(404)
        csp = _widget_frame_csp(request.host_url.rstrip("/"))
        data = _resolve_widget_data(widget.data_query)
        response = make_response(
            render_template("pages/widget_frame.html", widget=widget, data=data, csp=csp)
        )
        response.headers["Content-Security-Policy"] = csp
        return response

    @app.route("/pages/<int:page_id>/widgets/preview", methods=["POST"])
    def pages_widget_preview(page_id: int):
        """Render a grid-item shell for *unsaved* widget content posted in the body
        — the frame's data is resolved server-side and inlined as the iframe's
        srcdoc, so generated/edited drafts render live without writing to the store
        (Save is the only write). The client drops this straight onto its grid."""
        page = page_repo.get_page(db.session, page_id)
        if page is None:
            abort(404)
        payload = request.get_json(silent=True) or {}
        # A transient (un-persisted) Widget purely to feed the template; never
        # added to the session, so Save remains the only write.
        widget = Widget(
            id=str(payload.get("id") or page_repo.new_widget_id()),
            title=payload.get("title") or "",
            data_query=payload.get("data_query") or {},
            state=payload.get("state") or {},
            html=payload.get("html") or "",
            css=payload.get("css") or "",
            js=payload.get("js") or "",
            grid_x=0,
            grid_y=0,
            grid_w=int(payload.get("grid_w", 4)),
            grid_h=int(payload.get("grid_h", 3)),
        )
        csp = _widget_frame_csp(request.host_url.rstrip("/"))
        data = _resolve_widget_data(widget.data_query)
        frame_srcdoc = render_template("pages/widget_frame.html", widget=widget, data=data, csp=csp)
        return render_template(
            "pages/_widget.html", widget=widget, page=page, editable=True, frame_srcdoc=frame_srcdoc
        )

    @app.route("/pages/<int:page_id>/versions/<int:version_id>/widgets/<widget_id>/delete", methods=["POST"])
    def pages_version_delete_widget(page_id: int, version_id: int, widget_id: str):
        """Delete a widget from the version the design page is editing (passed in
        explicitly), so the published page is never touched while a draft is open."""
        _version_on_page(page_id, version_id)
        page_repo.remove_version_widget(db.session, version_id, widget_id)
        return "", 204

    @app.route("/pages/<int:page_id>/versions/<int:version_id>/widgets/<widget_id>/query", methods=["POST"])
    def pages_version_widget_query(page_id: int, version_id: int, widget_id: str):
        """Broker live query for a widget on the displayed version. Data resolution is
        version-independent (it hits the photo library), but the widget existence
        check is scoped to the exact version that asked."""
        _version_on_page(page_id, version_id)
        if page_repo.get_version_widget(db.session, version_id, widget_id) is None:
            abort(404)
        payload = request.get_json(silent=True) or {}
        # AI-influenced, so it runs the same validation; fail closed to null data on
        # an invalid query rather than erroring the widget.
        try:
            return {"data": resolve_query(db.session, payload.get("query", {}))}
        except ValueError:
            return {"data": None}

    @app.route("/pages/<int:page_id>/versions/<int:version_id>/widgets/<widget_id>/state", methods=["POST"])
    def pages_version_widget_state(page_id: int, version_id: int, widget_id: str):
        """Persist a widget's runtime state on the displayed version's widget (unique
        per version), so a draft's state never leaks onto the published page."""
        _version_on_page(page_id, version_id)
        if page_repo.get_version_widget(db.session, version_id, widget_id) is None:
            abort(404)
        payload = request.get_json(silent=True) or {}
        page_repo.set_version_widget_state(db.session, version_id, widget_id, payload.get("state", {}))
        return "", 204

    @app.route("/pages/<int:page_id>/chat", methods=["POST"])
    def pages_chat(page_id: int):
        """Start or continue an async generation, returning the working version id at
        once. With no working version, fork one (seeded from the client's current
        widget set, so unsaved manual edits carry in). With one already in review
        (READY/FAILED), continue on it — commit the client's current widgets (manual
        moves) and re-run — so the user can iterate on the draft. The run lives in the
        version, not this request, so it survives tab close / disconnect / timeouts."""
        page = page_repo.get_page(db.session, page_id)
        if page is None:
            abort(404)
        payload = request.get_json(silent=True) or {}
        message = (payload.get("message") or "").strip()
        if not message:
            return {"error": "Message is required."}, 400
        if llm_config.get_api_key() is None:
            return {"error": "No API key configured. Add your Anthropic API key in Settings → AI Generation."}, 400

        # Runtime errors collected client-side, fed back so the model can repair code
        # that threw. `widgets` is the client's current set (layout + any draft
        # content); None means "leave the version's widgets as-is".
        widget_errors = payload.get("widget_errors") or {}
        widgets = payload.get("widgets")
        working = page.working_version
        if working is not None and working.status == PAGE_VERSION_STATUS_IN_PROGRESS:
            return {"error": "A generation is already running."}, 409
        if working is not None:
            # Follow-up on the existing working draft: capture manual edits, then re-run.
            if widgets is not None:
                page_repo.save_version_widgets(db.session, working.id, widgets)
            page_repo.restart_version(db.session, working.id)
            version = working
        else:
            version = page_repo.fork_version(db.session, page_id, widgets=widgets)
        page_repo.add_version_message(db.session, version.id, "user", message)
        generate_page_task(version.id, message, widget_errors)
        return {"version_id": version.id}, 202

    def _version_on_page(page_id: int, version_id: int):
        version = page_repo.get_version(db.session, version_id)
        if version is None or version.page_id != page_id:
            abort(404)
        return version

    @app.route("/pages/<int:page_id>/versions/<int:version_id>/status", methods=["GET"])
    def pages_version_status(page_id: int, version_id: int):
        """Poll an in-flight generation: status, the conversation feed, and the
        version's widgets (rendered client-side via the preview route)."""
        return version_status_payload(_version_on_page(page_id, version_id))

    @app.route("/pages/<int:page_id>/versions/<int:version_id>/cancel", methods=["POST"])
    def pages_version_cancel(page_id: int, version_id: int):
        """Cancel a generation: flag CANCELLED (the running task polls this and
        stops), then delete the version and revert — the published version is
        untouched, so the UI snaps back to it."""
        _version_on_page(page_id, version_id)
        page_repo.set_version_status(db.session, version_id, PAGE_VERSION_STATUS_CANCELLED)
        page_repo.delete_version(db.session, version_id)
        return "", 204

    @app.route("/pages/<int:page_id>/versions/<int:version_id>/publish", methods=["POST"])
    def pages_version_publish(page_id: int, version_id: int):
        """Save (accept) a finished generation: commit the client's current widgets
        (manual moves made during review) onto the version, then publish it as the
        live page. Enabled only when the version is READY."""
        version = _version_on_page(page_id, version_id)
        if version.status != PAGE_VERSION_STATUS_READY:
            return {"error": "Version is not ready to publish."}, 409
        widgets = (request.get_json(silent=True) or {}).get("widgets")
        if widgets is not None:
            page_repo.save_version_widgets(db.session, version_id, widgets)
        page_repo.publish_version(db.session, version_id)
        return "", 204