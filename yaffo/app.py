import os
import logging
from pathlib import Path
from typing import Optional

from flask import Flask, request
from werkzeug.middleware.proxy_fix import ProxyFix
from yaffo import themes
from yaffo.config import get_int as get_config_int
from yaffo.db import db
from yaffo.common import DB_PATH
from yaffo.distance_units import supported_distance_unit_options
from yaffo.demo import configure_demo, init_demo_boundary
from yaffo.i18n import init_i18n, select_locale, supported_locale_options, text_direction
from yaffo.logging_config import get_logger
from yaffo.security import init_request_security
from yaffo.template_filters import init_template_filters
from yaffo.routes.init_routes import init_routes

logger = get_logger(__name__, 'webapp')

def create_app(db_path: Path = DB_PATH, config: Optional[dict] = None,
               startup_error: Optional[BaseException] = None):
    """Build the Flask app.

    `startup_error` puts it in recovery mode: something that had to succeed before
    serving (migrations, so far) didn't, so every request answers with the error
    screen instead of a traceback — or worse, a half-working app writing to a
    database whose shape it has guessed wrong.
    """
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

    configure_demo(app)
    trusted_hosts = os.environ.get("YAFFO_TRUSTED_HOSTS", "").split(",")
    trusted_hosts = [host.strip() for host in trusted_hosts if host.strip()]
    if trusted_hosts:
        app.config["TRUSTED_HOSTS"] = trusted_hosts

    try:
        proxy_hops = int(os.environ.get("YAFFO_PROXY_HOPS", "0"))
    except ValueError as exc:
        raise RuntimeError("YAFFO_PROXY_HOPS must be 0 or 1") from exc
    if proxy_hops not in (0, 1):
        raise RuntimeError("YAFFO_PROXY_HOPS must be 0 or 1")
    if proxy_hops:
        app.wsgi_app = ProxyFix(
            app.wsgi_app,
            x_for=proxy_hops,
            x_proto=proxy_hops,
            x_host=proxy_hops,
            x_port=proxy_hops,
        )

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
            "distance_unit_options": supported_distance_unit_options(),
            "supported_locales": supported_locale_options(),
            "text_direction": text_direction(locale),
        }

    # Register template filters

    init_template_filters(app)
    init_routes(app)
    init_request_security(app)
    init_demo_boundary(app)

    if startup_error is not None:
        from yaffo.routes.base import render_critical_error

        @app.before_request
        def serve_startup_error():
            # Static files still serve: the error screen needs its stylesheet.
            if request.endpoint == "static":
                return None
            return render_critical_error(startup_error)

    # `python -m yaffo` starts the p2p sharing engine itself (see __main__);
    # under `flask run` (the inv app-local / start-app dev flow) there is no
    # __main__, so create_app starts it when the invoke env opts in. Never set
    # for tests — they inject a service (or none) explicitly.
    #
    # Reloader caveat: with FLASK_DEBUG=1, `flask run` loads the app in BOTH
    # the reloader parent and the serving child (Flask ≥2.2). Only the child
    # (WERKZEUG_RUN_MAIN=true) may start the engine — otherwise the parent
    # wins the UDP-port bind and the process actually serving requests gets
    # "Address already in use" and sharing disabled.
    reloader_parent = (
        os.environ.get("FLASK_DEBUG") == "1" and os.environ.get("WERKZEUG_RUN_MAIN") != "true"
    )
    if os.environ.get("YAFFO_P2P_ENABLED") == "1" and not app.config.get("TESTING") and not reloader_parent:
        from yaffo.p2p.service import start_p2p_service

        start_p2p_service(app)

    return app

if __name__ == "__main__":
    app = create_app()
    # threaded so a long streaming response (e.g. the index-photos scan) doesn't block
    # the page's other requests on the single-user dev server. Default port 5001, not 5000 —
    # macOS AirPlay Receiver (Control Center) binds *:5000 and answers with a 403.
    app.run(debug=True, threaded=True, port=get_config_int("web", "port", 5001))
