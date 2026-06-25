import os
import logging
from pathlib import Path
from typing import Optional

from flask import Flask
from yaffo import themes
from yaffo.db import db
from yaffo.common import DB_PATH
from yaffo.i18n import init_i18n, select_locale, supported_locale_options, text_direction
from yaffo.logging_config import get_logger
from yaffo.template_filters import init_template_filters
from yaffo.routes.init_routes import init_routes

logger = get_logger(__name__, 'webapp')

def create_app(db_path: Path = DB_PATH, config: Optional[dict] = None):
    app = Flask(__name__)

    # Configure werkzeug logger to use our logging system
    werkzeug_logger = logging.getLogger('werkzeug')
    werkzeug_logger.setLevel(logging.ERROR)

    # Set Flask app logger to use our webapp logger
    app.logger.handlers = logger.handlers
    app.logger.setLevel(logger.level)

    logger.info("Starting Photo Organizer application")
    app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{db_path}"
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.config["SQLALCHEMY_ECHO"] = os.environ.get("SQLALCHEMY_ECHO", "").lower() == "true"
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["SESSION_COOKIE_SECURE"] = False
    app.config["SESSION_COOKIE_HTTPONLY"] = True

    app.secret_key = os.environ.get('SECRET_KEY', 'dev-secret-key-change-in-production')
    app.config['SESSION_TYPE'] = 'filesystem'  # or 'redis', 'memcached', etc.
    app.config['SESSION_PERMANENT'] = True
    #app.config['SESSION_USE_SIGNER'] = True

    # Caller overrides (e.g. tests) win over the defaults above.
    if config:
        app.config.update(config)

    db.init_app(app)
    init_i18n(app)

    # Make url_map available in all templates
    @app.context_processor
    def inject_url_map():
        return {'url_map': app.url_map}

    @app.context_processor
    def inject_theme():
        return {'theme': themes.get_theme()}

    @app.context_processor
    def inject_i18n():
        locale = select_locale()
        return {
            "current_locale": locale,
            "supported_locales": supported_locale_options(),
            "text_direction": text_direction(locale),
        }

    # Register template filters

    init_template_filters(app)
    init_routes(app)
    return app

if __name__ == "__main__":
    app = create_app()
    # threaded so a long streaming response (e.g. the index-photos scan) doesn't block
    # the page's other requests on the single-user dev server. Port 5001, not 5000 —
    # macOS AirPlay Receiver (Control Center) binds *:5000 and answers with a 403.
    app.run(debug=True, threaded=True, port=5001)
