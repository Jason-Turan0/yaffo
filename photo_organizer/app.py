from flask import Flask
from photo_organizer.db import db
from photo_organizer.common import DB_PATH

def create_app():
    app = Flask(__name__)
    app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{DB_PATH}"
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False  # optional, avoids warnings
    app.config["SQLALCHEMY_ECHO"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "None"
    app.config["SESSION_COOKIE_SECURE"] = False
    app.config["SESSION_COOKIE_HTTPONLY"] = True

    db.init_app(app)

    from photo_organizer.routes.init_routes import init_routes
    init_routes(app)
    return app

if __name__ == "__main__":
    app = create_app()
    app.run(debug=True)
