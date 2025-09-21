from flask import render_template, Flask

from photo_organizer.db.models import Photo


def init_index_route(app: Flask):
    @app.route("/")
    def index():
        photos = Photo.query.limit(20).all()
        return render_template("index.html", photos=photos)