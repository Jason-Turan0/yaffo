from flask import Flask

from yaffo.routes.base import init_base_routes
from yaffo.routes.faces import init_faces_routes
from yaffo.routes.home import init_home_routes
from yaffo.routes.jobs import init_jobs_routes
from yaffo.routes.locations import init_locations_routes
from yaffo.routes.pages import init_pages_routes
from yaffo.routes.people import init_people_routes
from yaffo.routes.photos import init_photos_routes
from yaffo.routes.utilities import init_utilities_routes
from yaffo.routes.settings import init_settings_routes


def init_routes(app: Flask):
    init_base_routes(app)
    init_home_routes(app)
    init_photos_routes(app)
    init_people_routes(app)
    init_locations_routes(app)
    init_faces_routes(app)
    init_jobs_routes(app)
    init_utilities_routes(app)
    init_settings_routes(app)
    init_pages_routes(app)
