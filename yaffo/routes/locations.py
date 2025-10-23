import os
import requests
from flask import Flask, render_template, jsonify, request
from sqlalchemy import func

from yaffo.db import db
from yaffo.db.models import Photo

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
                Photo.full_file_path
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

    @app.route("/locations/reverse-geocode", methods=["POST"])
    def reverse_geocode():
        """Reverse geocode a lat/lon coordinate using OpenStreetMap Nominatim"""
        data = request.get_json()
        lat = data.get('lat')
        lon = data.get('lon')

        if lat is None or lon is None:
            return jsonify({'error': 'Invalid request'}), 400

        try:
            osm_response = requests.get(
                "https://nominatim.openstreetmap.org/reverse",
                params={
                    "lat": lat,
                    "lon": lon,
                    "format": "json"
                },
                headers={
                    "User-Agent": "PhotoOrganizer/1.0"
                },
                timeout=3
            )

            if osm_response.status_code == 200:
                osm_data = osm_response.json()
                address = osm_data.get('address', {})

                location_parts = []
                for key in ['city', 'town', 'village', 'county', 'state', 'country']:
                    if key in address:
                        location_parts.append(address[key])

                location_name = ', '.join(location_parts) if location_parts else osm_data.get('display_name', '')

                return jsonify({
                    'success': True,
                    'location_name': location_name,
                    'display_name': osm_data.get('display_name')
                })
            else:
                return jsonify({'error': 'Geocoding failed'}), 500
        except requests.RequestException as e:
            return jsonify({'error': str(e)}), 500