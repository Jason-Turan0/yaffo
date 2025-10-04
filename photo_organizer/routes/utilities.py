from flask import render_template, Flask


def init_utilities_routes(app: Flask):
    @app.route("/utilities", methods=["GET"])
    def utilities_index():
        return render_template("utilities/index.html")