import os
import logging

from flask import Flask
from photo_organizer.db import db
from photo_organizer.common import DB_PATH
from photo_organizer.logging_config import get_logger

logger = get_logger(__name__, 'webapp')

def create_app():
    app = Flask(__name__)

    # Configure werkzeug logger to use our logging system
    werkzeug_logger = logging.getLogger('werkzeug')
    werkzeug_logger.setLevel(logging.ERROR)

    # Set Flask app logger to use our webapp logger
    app.logger.handlers = logger.handlers
    app.logger.setLevel(logger.level)

    logger.info("Starting Photo Organizer application")
    app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{DB_PATH}"
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False  # optional, avoids warnings
    app.config["SQLALCHEMY_ECHO"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["SESSION_COOKIE_SECURE"] = False
    app.config["SESSION_COOKIE_HTTPONLY"] = True

    app.secret_key = os.environ.get('SECRET_KEY', 'dev-secret-key-change-in-production')
    app.config['SESSION_TYPE'] = 'filesystem'  # or 'redis', 'memcached', etc.
    app.config['SESSION_PERMANENT'] = True
    #app.config['SESSION_USE_SIGNER'] = True

    db.init_app(app)

    # Make url_map available in all templates
    @app.context_processor
    def inject_url_map():
        return {'url_map': app.url_map}

    # Register template filters
    from photo_organizer.template_filters import init_template_filters
    init_template_filters(app)

    from photo_organizer.routes.init_routes import init_routes
    init_routes(app)
    return app

if __name__ == "__main__":
    app = create_app()
    app.run(debug=True)
