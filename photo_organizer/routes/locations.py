import os
from flask import Flask, render_template, jsonify
from sqlalchemy import func

from photo_organizer.db import db
from photo_organizer.db.models import Photo

def init_locations_routes(app: Flask):
    @app.route("/locations", methods=["GET"])
    def locations_list():
        """List all locations"""
        locations = (
            db.session.query(
                Photo.id,
                Photo.location_name,
                Photo.latitude,
                Photo.longitude,
                Photo.relative_file_path
            )
            .filter(Photo.latitude.isnot(None))
            .filter(Photo.longitude.isnot(None))
            .all()
        )

        locations_data = [
            {
                'id': loc.id,
                'name': loc.location_name,
                'lat': float(loc.latitude),
                'lon': float(loc.longitude),
                'photo_path': loc.relative_file_path,
                'filename': os.path.basename(loc.relative_file_path)
            }
            for loc in locations
        ]

        return render_template("locations/list.html", locations=locations_data)