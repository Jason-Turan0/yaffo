from flask import Flask, render_template, request, redirect, url_for, abort, make_response

from yaffo.page_builder import stub_store
from yaffo.utils.context import context


# Sandboxed widget frames may only run their own inline code and load images;
# no network (connect-src 'none') so injected data can't be exfiltrated.
_WIDGET_FRAME_CSP = (
    "default-src 'none'; img-src data:; style-src 'unsafe-inline'; "
    "script-src 'unsafe-inline'; connect-src 'none'"
)


@context("yaffo-pages")
def init_pages_routes(app: Flask):
    @app.context_processor
    def inject_nav_pages():
        return {"nav_pages": stub_store.list_pages()}

    @app.route("/pages", methods=["POST"])
    def pages_create():
        title = (request.form.get("title") or "").strip() or "Untitled Page"
        theme_prompt = (request.form.get("theme_prompt") or "").strip()
        page = stub_store.create_page(title=title, theme_prompt=theme_prompt)
        return redirect(url_for("pages_detail", page_id=page.id))

    @app.route("/pages/<int:page_id>", methods=["GET"])
    def pages_detail(page_id: int):
        page = stub_store.get_page(page_id)
        if page is None:
            abort(404)
        if not page.widgets:
            return redirect(url_for("pages_design", page_id=page_id))
        return render_template("pages/presentation.html", page=page)

    @app.route("/pages/<int:page_id>/design", methods=["GET"])
    def pages_design(page_id: int):
        page = stub_store.get_page(page_id)
        if page is None:
            abort(404)
        return render_template("pages/design.html", page=page)

    @app.route("/pages/<int:page_id>/update", methods=["POST"])
    def pages_update(page_id: int):
        if stub_store.get_page(page_id) is None:
            abort(404)
        payload = request.get_json(silent=True) or {}
        title = (payload.get("title") or "").strip() or "Untitled Page"
        theme_prompt = (payload.get("theme_prompt") or "").strip()
        show_title = bool(payload.get("show_title", True))
        stub_store.update_page(page_id, title=title, theme_prompt=theme_prompt, show_title=show_title)
        stub_store.update_layout(page_id, payload.get("layout", []))
        return "", 204

    @app.route("/pages/<int:page_id>/delete", methods=["POST"])
    def pages_delete(page_id: int):
        stub_store.delete_page(page_id)
        return redirect(url_for("index"))

    @app.route("/pages/<int:page_id>/widgets", methods=["POST"])
    def pages_add_widget(page_id: int):
        widget = stub_store.add_widget(page_id)
        if widget is None:
            abort(404)
        return render_template(
            "pages/_widget.html", widget=widget, page=stub_store.get_page(page_id), editable=True
        )

    @app.route("/pages/<int:page_id>/widgets/<int:widget_id>/frame", methods=["GET"])
    def pages_widget_frame(page_id: int, widget_id: int):
        page = stub_store.get_page(page_id)
        if page is None:
            abort(404)
        widget = next((w for w in page.widgets if w.id == widget_id), None)
        if widget is None:
            abort(404)
        data = stub_store.resolve_data(widget.data_query)
        response = make_response(render_template("pages/widget_frame.html", widget=widget, data=data))
        response.headers["Content-Security-Policy"] = _WIDGET_FRAME_CSP
        return response

    @app.route("/pages/<int:page_id>/widgets/<int:widget_id>/delete", methods=["POST"])
    def pages_delete_widget(page_id: int, widget_id: int):
        stub_store.remove_widget(page_id, widget_id)
        return "", 204

    @app.route("/pages/<int:page_id>/chat", methods=["POST"])
    def pages_chat(page_id: int):
        page = stub_store.get_page(page_id)
        if page is None:
            abort(404)
        payload = request.get_json(silent=True) or {}
        message = (payload.get("message") or "").strip()
        widget = None
        if message:
            stub_store.add_message(page_id, "user", message)
            widget = stub_store.generate_widget(page_id, message)
            stub_store.add_message(
                page_id,
                "assistant",
                f"Added the “{widget.title}” widget to your page. "
                "Tell me what to change, or describe another widget. "
                "(Mock response — real generation isn't wired up yet.)",
            )
        widget_html = (
            render_template("pages/_widget.html", widget=widget, page=page, editable=True)
            if widget
            else None
        )
        return {
            "messages_html": render_template("pages/_messages.html", page=page),
            "widget_html": widget_html,
        }