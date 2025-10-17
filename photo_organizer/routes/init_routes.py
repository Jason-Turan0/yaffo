from flask import Flask

from photo_organizer.routes.faces import init_faces_routes
from photo_organizer.routes.home import init_home_routes
from photo_organizer.routes.locations import init_locations_routes
from photo_organizer.routes.people import init_people_routes
from photo_organizer.routes.photos import init_photos_routes
from photo_organizer.routes.utilities import init_utilities_routes


def init_routes(app: Flask):
    init_home_routes(app)
    init_photos_routes(app)
    init_people_routes(app)
    init_locations_routes(app)
    init_faces_routes(app)
    init_utilities_routes(app)
