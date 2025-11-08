from flask import Flask, redirect, url_for


def init_base_utilities_routes(app: Flask):
    @app.route("/utilities", methods=["GET"])
    def utilities_index():
        return redirect(url_for('utilities_index_photos'))