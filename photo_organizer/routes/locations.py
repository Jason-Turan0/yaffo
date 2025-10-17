import os
from flask import Flask, render_template, jsonify, request
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

    @app.route("/locations/bulk-update", methods=["POST"])
    def locations_bulk_update():
        """Bulk update location names for multiple photos"""
        data = request.get_json()
        photo_ids = data.get('photo_ids', [])
        location_name = data.get('location_name', '').strip()

        if not photo_ids or not location_name:
            return jsonify({'error': 'Invalid request'}), 400

        try:
            updated_count = (
                db.session.query(Photo)
                .filter(Photo.id.in_(photo_ids))
                .update({'location_name': location_name}, synchronize_session=False)
            )
            db.session.commit()

            return jsonify({
                'success': True,
                'updated_count': updated_count,
                'location_name': location_name
            })
        except Exception as e:
            db.session.rollback()
            return jsonify({'error': str(e)}), 500