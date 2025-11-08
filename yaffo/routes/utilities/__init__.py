from flask import Flask

from yaffo.routes.utilities.auto_assign import init_auto_assign_routes
from yaffo.routes.utilities.base import init_base_utilities_routes
from yaffo.routes.utilities.index_photos import init_index_photos_routes
from yaffo.routes.utilities.organize_photos import init_organize_photos_routes
from yaffo.routes.utilities.sync_metadata import init_sync_metadata_routes


def init_utilities_routes(app: Flask):
    init_base_utilities_routes(app)
    init_index_photos_routes(app)
    init_auto_assign_routes(app)
    init_sync_metadata_routes(app)
    init_organize_photos_routes(app)