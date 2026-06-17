from flask import Flask

from yaffo.routes.utilities.base import init_base_utilities_routes
from yaffo.routes.utilities.index_photos import init_index_photos_routes
from yaffo.routes.utilities.remove_duplicates import init_remove_duplicates_routes
from yaffo.routes.utilities.automations import init_automations_routes


def init_utilities_routes(app: Flask):
    init_base_utilities_routes(app)
    init_index_photos_routes(app)
    init_remove_duplicates_routes(app)
    init_automations_routes(app)
