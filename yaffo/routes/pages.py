from flask import Flask, render_template, request, redirect, url_for, abort

from yaffo.page_builder import stub_store
from yaffo.utils.context import context


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
        return render_template("pages/detail.html", page=page)

    @app.route("/pages/<int:page_id>/update", methods=["POST"])
    def pages_update(page_id: int):
        if stub_store.get_page(page_id) is None:
            abort(404)
        title = (request.form.get("title") or "").strip() or "Untitled Page"
        theme_prompt = (request.form.get("theme_prompt") or "").strip()
        stub_store.update_page(page_id, title=title, theme_prompt=theme_prompt)
        return redirect(url_for("pages_detail", page_id=page_id))

    @app.route("/pages/<int:page_id>/delete", methods=["POST"])
    def pages_delete(page_id: int):
        stub_store.delete_page(page_id)
        return redirect(url_for("index"))

    @app.route("/pages/<int:page_id>/blocks", methods=["POST"])
    def pages_add_block(page_id: int):
        block = stub_store.add_block(page_id)
        if block is None:
            abort(404)
        return render_template("pages/_block.html", blk=block)

    @app.route("/pages/<int:page_id>/blocks/<int:block_id>/delete", methods=["POST"])
    def pages_delete_block(page_id: int, block_id: int):
        stub_store.remove_block(page_id, block_id)
        return "", 204

    @app.route("/pages/<int:page_id>/layout", methods=["POST"])
    def pages_layout(page_id: int):
        if stub_store.get_page(page_id) is None:
            abort(404)
        payload = request.get_json(silent=True) or {}
        stub_store.update_layout(page_id, payload.get("layout", []))
        return "", 204