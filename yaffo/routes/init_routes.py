from flask import Flask

from yaffo.routes.base import init_base_routes
from yaffo.routes.faces import init_faces_routes
from yaffo.routes.filter_config import init_filter_config_routes
from yaffo.routes.home import init_home_routes
from yaffo.routes.jobs import init_jobs_routes
from yaffo.routes.locations import init_locations_routes
from yaffo.routes.pages import init_pages_routes
from yaffo.routes.people import init_people_routes
from yaffo.routes.media import init_media_routes
from yaffo.routes.utilities import init_utilities_routes
from yaffo.routes.settings import init_settings_routes
from yaffo.routes.themes_page import init_themes_page_routes


def init_routes(app: Flask):
    init_base_routes(app)
    init_home_routes(app)
    init_filter_config_routes(app)
    init_media_routes(app)
    init_people_routes(app)
    init_locations_routes(app)
    init_faces_routes(app)
    init_jobs_routes(app)
    init_utilities_routes(app)
    init_settings_routes(app)
    init_themes_page_routes(app)
    init_pages_routes(app)
