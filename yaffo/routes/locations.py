import os
from flask import Flask, render_template, jsonify, request
from flask_babel import gettext
from sqlalchemy.orm import selectinload

from yaffo.db import db
from yaffo.db.models import Face, MediaItem, MEDIA_STATUS_INDEXED, EVENT_MEDIA_MODIFIED
from yaffo.background_tasks.events import emit_event
from yaffo.routes import filter_config
from yaffo.routes.filter_panel import build_filters_context
from yaffo.utils.reverse_geocode import reverse_geocode


def _location_payload(media_item: MediaItem) -> dict:
    """One map marker's JSON. Besides what the popup renders, it carries every
    field the sidebar can filter on — filtering happens client-side here
    (static/filters/client_filter.js), unlike the home gallery's SQL filters."""
    person_ids: set[int] = set()
    genders: set[int] = set()
    for face in media_item.faces:
        # Same fallback as the home route's gender filter: a person's declared
        # gender overrides the face estimate; unassigned faces use the estimate.
        if face.people:
            for person in face.people:
                person_ids.add(person.id)
                gender = person.gender if person.gender is not None else face.gender
                if gender is not None:
                    genders.add(gender)
        elif face.gender is not None:
            genders.add(face.gender)

    return {
        'id': media_item.id,
        'name': media_item.location_name,
        'lat': float(media_item.latitude),
        'lon': float(media_item.longitude),
        'photo_path': media_item.full_file_path,
        'filename': os.path.basename(media_item.full_file_path),
        # The map popup renders this as an <img>; a video's /media/<id> is the
        # raw clip, so the client must use the poster route instead.
        'media_type': media_item.media_type,
        'year': media_item.year,
        'month': media_item.month,
        'device': media_item.device,
        'favorite': bool(media_item.favorite),
        'person_ids': sorted(person_ids),
        'genders': sorted(genders),
        'label_ids': sorted({media_label.label_id for media_label in media_item.labels}),
        'tags': [{'name': tag.tag_name, 'value': tag.tag_value} for tag in media_item.tags],
    }


def init_locations_routes(app: Flask):
    @app.route("/locations", methods=["GET"])
    def locations_list():
        """List all locations"""
        media_items = (
            db.session.query(MediaItem)
            .options(
                selectinload(MediaItem.faces).selectinload(Face.people),
                selectinload(MediaItem.labels),
                selectinload(MediaItem.tags),
            )
            .filter(MediaItem.latitude.isnot(None))
            .filter(MediaItem.longitude.isnot(None))
            .all()
        )

        locations_data = [_location_payload(media_item) for media_item in media_items]

        return render_template(
            "locations/list.html",
            locations=locations_data,
            filters=build_filters_context(db.session, request.args),
            filter_layout=filter_config.load_layout(db.session, page="locations"),
            filter_default_keys=filter_config.default_keys(),
        )

    @app.route("/locations/bulk-update", methods=["POST"])
    def locations_bulk_update():
        """Bulk update (or, with clear=true, remove) location names for multiple
        photos. Clearing is an explicit flag rather than an empty name so a blank
        input can never silently wipe names."""
        data = request.get_json(silent=True) or {}
        media_item_ids = data.get('media_item_ids', [])
        clear = data.get('clear') is True
        raw_location_name = data.get('location_name')
        location_name = raw_location_name.strip() if isinstance(raw_location_name, str) else ""

        if not media_item_ids or (not location_name and not clear):
            return jsonify({
                'error': gettext("Media item IDs and location name are required"),
                'code': 'location_fields_required',
            }), 400

        try:
            updated_count = (
                db.session.query(MediaItem)
                .filter(MediaItem.id.in_(media_item_ids))
                .update({
                    'location_name': None if clear else location_name,
                    'status': MEDIA_STATUS_INDEXED
                }, synchronize_session=False)
            )
            db.session.commit()

            emit_event(EVENT_MEDIA_MODIFIED, {"media_item_ids": media_item_ids})
            return jsonify({
                'success': True,
                'updated_count': updated_count,
                'location_name': None if clear else location_name
            })
        except Exception:
            db.session.rollback()
            return jsonify({
                'error': gettext("Could not update locations"),
                'code': 'location_update_failed',
            }), 500

    @app.route("/locations/reverse-geocode", methods=["POST"])
    def reverse_geocode_route():
        """Reverse geocode a lat/lon coordinate using OpenStreetMap Nominatim"""
        data = request.get_json(silent=True) or {}
        lat = data.get('lat')
        lon = data.get('lon')

        if lat is None or lon is None:
            return jsonify({
                'error': gettext("Latitude and longitude are required"),
                'code': 'coordinates_required',
            }), 400

        location_name = reverse_geocode(lat, lon)
        if location_name is None:
            return jsonify({
                'error': gettext("Could not determine a location name"),
                'code': 'reverse_geocode_failed',
            }), 500

        return jsonify({
            'success': True,
            'location_name': location_name,
        })
