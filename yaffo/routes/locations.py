import os
from flask import Flask, render_template, jsonify, request
from sqlalchemy import func

from yaffo.db import db
from yaffo.db.models import MediaItem, MEDIA_STATUS_INDEXED, EVENT_MEDIA_MODIFIED
from yaffo.background_tasks.events import emit_event
from yaffo.utils.reverse_geocode import reverse_geocode

def init_locations_routes(app: Flask):
    @app.route("/locations", methods=["GET"])
    def locations_list():
        """List all locations"""
        locations = (
            db.session.query(
                MediaItem.id,
                MediaItem.location_name,
                MediaItem.latitude,
                MediaItem.longitude,
                MediaItem.full_file_path
            )
            .filter(MediaItem.latitude.isnot(None))
            .filter(MediaItem.longitude.isnot(None))
            .all()
        )

        locations_data = [
            {
                'id': loc.id,
                'name': loc.location_name,
                'lat': float(loc.latitude),
                'lon': float(loc.longitude),
                'photo_path': loc.full_file_path,
                'filename': os.path.basename(loc.full_file_path)
            }
            for loc in locations
        ]

        return render_template("locations/list.html", locations=locations_data)

    @app.route("/locations/bulk-update", methods=["POST"])
    def locations_bulk_update():
        """Bulk update location names for multiple photos"""
        data = request.get_json()
        media_item_ids = data.get('media_item_ids', [])
        location_name = data.get('location_name', '').strip()

        if not media_item_ids or not location_name:
            return jsonify({'error': 'Invalid request'}), 400

        try:
            updated_count = (
                db.session.query(MediaItem)
                .filter(MediaItem.id.in_(media_item_ids))
                .update({
                    'location_name': location_name,
                    'status': MEDIA_STATUS_INDEXED
                }, synchronize_session=False)
            )
            db.session.commit()

            emit_event(EVENT_MEDIA_MODIFIED, {"media_item_ids": media_item_ids})
            return jsonify({
                'success': True,
                'updated_count': updated_count,
                'location_name': location_name
            })
        except Exception as e:
            db.session.rollback()
            return jsonify({'error': str(e)}), 500

    @app.route("/locations/reverse-geocode", methods=["POST"])
    def reverse_geocode_route():
        """Reverse geocode a lat/lon coordinate using OpenStreetMap Nominatim"""
        data = request.get_json()
        lat = data.get('lat')
        lon = data.get('lon')

        if lat is None or lon is None:
            return jsonify({'error': 'Invalid request'}), 400

        location_name = reverse_geocode(lat, lon)
        if location_name is None:
            return jsonify({'error': 'Geocoding failed'}), 500

        return jsonify({
            'success': True,
            'location_name': location_name,
        })