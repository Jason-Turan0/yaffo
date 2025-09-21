from flask import Flask

from photo_organizer.routes.faces import init_faces_route
from photo_organizer.routes.index import init_index_route
from photo_organizer.routes.people import init_people_route
from photo_organizer.routes.photos import init_photos_routes


def init_routes(app: Flask):
    init_index_route(app)
    init_photos_routes(app)
    init_people_route(app)
    init_faces_route(app)
