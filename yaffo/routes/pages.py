import json
import logging

from flask import (
    Flask,
    Response,
    render_template,
    request,
    redirect,
    stream_with_context,
    url_for,
    abort,
    make_response,
)

from yaffo.db import db
from yaffo.db.models import Widget
from yaffo.db.repositories import custom_page_repository as page_repo
from yaffo.db.repositories.data_query_repository import resolve_data_query, resolve_query
from yaffo.logging_config import get_logger
from yaffo.page_builder import llm_config, schemas
from yaffo.page_builder.agent import create_agent
from yaffo.page_builder.prompt_generator import build_user_message
from yaffo.page_builder.widget_api import widget_api_source
from yaffo.page_builder.widget_merge import merge_widget_content
from yaffo.utils.context import context

# Friendly progress text shown while a tool runs (keyed by tool name). Anything
# not listed falls back to a generic "Working…".
_TOOL_STATUS = {
    "create_widget": "Creating widget…",
    "update_widget": "Updating widget…",
    "run_data_query": "Looking up information…",
}

logger = get_logger(__name__)

def _ndjson(obj: dict) -> str:
    """One newline-delimited JSON record for the chat stream."""
    return json.dumps(obj) + "\n"


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

    @app.route("/pages/<int:page_id>/widgets/<widget_id>/delete", methods=["POST"])
    def pages_delete_widget(page_id: int, widget_id: str):
        page_repo.remove_widget(db.session, page_id, widget_id)
        return "", 204

    @app.route("/pages/<int:page_id>/widgets/<widget_id>/query", methods=["POST"])
    def pages_widget_query(page_id: int, widget_id: str):
        if page_repo.get_widget(db.session, page_id, widget_id) is None:
            abort(404)
        payload = request.get_json(silent=True) or {}
        # Live broker query — AI-influenced, so it runs the same validation; fail
        # closed to null data on an invalid query rather than erroring the widget.
        try:
            return {"data": resolve_query(db.session, payload.get("query", {}))}
        except ValueError:
            return {"data": None}

    @app.route("/pages/<int:page_id>/widgets/<widget_id>/state", methods=["POST"])
    def pages_widget_state(page_id: int, widget_id: str):
        if page_repo.get_widget(db.session, page_id, widget_id) is None:
            abort(404)
        payload = request.get_json(silent=True) or {}
        page_repo.set_widget_state(db.session, page_id, widget_id, payload.get("state", {}))
        return "", 204

    @app.route("/pages/<int:page_id>/chat", methods=["POST"])
    def pages_chat(page_id: int):
        page = page_repo.get_page(db.session, page_id)
        if page is None:
            abort(404)
        payload = request.get_json(silent=True) or {}
        message = (payload.get("message") or "").strip()
        # Runtime errors collected client-side, fed back so the model can repair
        # code that threw.
        widget_errors = payload.get("widget_errors") or {}

        # Stream the agent's progress to the browser as newline-delimited JSON:
        # one record per assistant message, tool status, and new/updated widget,
        # so the page fills in live instead of after the whole (slow) run. Widget
        # records carry the generated *content* (the tool's host payload) — nothing
        # is persisted; the client holds it as a draft until Save (see grid.js).
        # TODO test streaming in GCP environment.
        def generate():
            if not message:
                yield _ndjson(schemas.chat_done())
                return

            page_repo.add_message(db.session, page_id, "user", message)

            if llm_config.get_api_key() is None:
                reply = "No API key configured. Add your Anthropic API key in Settings → AI Generation."
                page_repo.add_message(db.session, page_id, "assistant", reply)
                yield _ndjson(schemas.chat_message(reply))
                yield _ndjson(schemas.chat_done())
                return

            client_widgets = payload.get("widgets")
            if client_widgets is None:
                client_widgets = [{"id": w.id} for w in page.widgets]
            current_widgets = merge_widget_content(page.widgets, client_widgets)

            user_message = build_user_message(
                message,
                page_title=page.title,
                page_subtitle=page.subtitle,
                widgets=current_widgets,
                widget_errors=widget_errors,
            )

            try:
                agent = create_agent(page_id, current_widgets=current_widgets)
                for event in agent.run_events(user_message):
                    if event.type == "assistant":
                        page_repo.add_message(db.session, page_id, "assistant", event.text)
                        yield _ndjson(schemas.chat_message(event.text))
                    elif event.type == "tool":
                        yield _ndjson(schemas.chat_status(_TOOL_STATUS.get(event.name, "Working…")))
                        # A widget tool returns the generated content -> stream it;
                        # the client renders it as a draft (create) or swaps it in
                        # (update). Nothing is written to the store here.
                        if event.tool_result_data and not event.is_error:
                            record = (schemas.chat_widget_updated if event.name == "update_widget"
                                      else schemas.chat_widget_new)
                            yield _ndjson(record(event.tool_result_data))
                    elif event.type == "error":
                        page_repo.add_message(db.session, page_id, "assistant", event.text)
                        yield _ndjson(schemas.chat_message(event.text))
            except Exception as exc:  # surface failures to the user, don't 500
                reply = f"Generation error: {exc}"
                page_repo.add_message(db.session, page_id, "assistant", reply)
                logger.error(f"Generation error: {exc}")
                yield _ndjson(schemas.chat_message(reply))

            yield _ndjson(schemas.chat_done())

        response = Response(stream_with_context(generate()), mimetype="application/x-ndjson")
        response.headers["Cache-Control"] = "no-cache"
        response.headers["X-Accel-Buffering"] = "no"  # don't let a proxy buffer the stream
        return response