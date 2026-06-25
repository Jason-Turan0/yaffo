import math
from pathlib import Path

import pydash as py_
import requests
from flask import Flask, jsonify, render_template, request
from flask_babel import gettext
from sqlalchemy import distinct, func
from sqlalchemy.orm import joinedload

from yaffo.db import db
from yaffo.db.models import (
    MEDIA_TYPE_PHOTO,
    MEDIA_TYPE_VIDEO,
    ClassificationLabel,
    Face,
    MediaItem,
    MediaLabel,
    Person,
    PersonFace,
    Tag,
)
from yaffo.db.repositories.media_repository import get_distinct_years, get_distinct_months
from yaffo.routes import filter_config
from yaffo.utils.context import context


def calculate_bounding_box(lat: float, lon: float, distance_miles: float) -> tuple[float, float, float, float]:
    """
    Calculate bounding box coordinates for a given center point and distance.
    Returns (min_lat, max_lat, min_lon, max_lon)
    """
    lat_degree_miles = 69.0
    lon_degree_miles = abs(math.cos(math.radians(lat)) * 69.0)

    lat_offset = distance_miles / lat_degree_miles
    lon_offset = distance_miles / lon_degree_miles

    min_lat = lat - lat_offset
    max_lat = lat + lat_offset
    min_lon = lon - lon_offset
    max_lon = lon + lon_offset

    return (min_lat, max_lat, min_lon, max_lon)


@context("yaffo-gallery")
def init_home_routes(app: Flask):
    @app.route("/", methods=["GET"])
    def index():
        # Get filter parameters
        path = request.args.get("path", type=str)
        path = path.strip() if path else None
        person_ids = request.args.getlist("person", type=int)
        person_match_type = request.args.get("person-match-type", default='any', type=str)
        gender = request.args.get("gender", type=int)
        labels_ids = request.args.getlist("labels", type=int)
        labels_match_type = request.args.get("labels-match-type", default='any', type=str)
        tag_name = request.args.get("tag-name", type=str)
        tag_value = request.args.get("tag-value", type=str)
        location_names = request.args.getlist("location", type=str)
        location_match_type = request.args.get("location-match-type", default='any', type=str)
        proximity_lat = request.args.get("proximity-lat", type=float)
        proximity_lon = request.args.get("proximity-lon", type=float)
        proximity_distance = request.args.get("proximity-distance", type=float)
        proximity_location = request.args.get("proximity-location", type=str)
        year = request.args.get("year", type=int)
        month = request.args.get("month", type=int)
        device = request.args.get("device", type=str)
        device = device.strip() if device else None
        favorite = request.args.get("favorite", type=int)
        media_type = request.args.get("media-type", type=str)
        if media_type not in (MEDIA_TYPE_PHOTO, MEDIA_TYPE_VIDEO):
            media_type = None
        page = request.args.get("page", default=1, type=int)
        page_size = request.args.get("page-size", type=int)
        filter_page_size = page_size if page_size else 25
        # Build query with eager loading
        query = (
            db.session.query(MediaItem)
            .options(joinedload(MediaItem.faces).joinedload(Face.people))
            .options(joinedload(MediaItem.labels).joinedload(MediaLabel.label))
            .order_by(MediaItem.date_taken.desc())
        )

        # Apply filters
        if path:
            # Partial, case-insensitive match on any part of the stored path
            # (folders or file name); autoescape so %/_ in the term stay literal.
            query = query.filter(MediaItem.full_file_path.icontains(path, autoescape=True))
        if year:
            query = query.filter(MediaItem.year == year)
        if month:
            query = query.filter(MediaItem.month == month)
        if device:
            query = query.filter(MediaItem.device == device)
        if favorite:
            query = query.filter(MediaItem.favorite.is_(True))
        if media_type:
            query = query.filter(MediaItem.media_type == media_type)
        if person_ids and person_match_type and len(person_ids) > 0:
            if person_match_type == 'all':
                # AND logic: Photo must contain ALL selected people
                for person_id in person_ids:
                    subquery = (
                        db.session.query(MediaItem.id)
                        .join(MediaItem.faces)
                        .join(Face.person_face)
                        .filter(PersonFace.person_id == person_id)
                    )
                    query = query.filter(MediaItem.id.in_(subquery))
            else:
                # OR logic: Photo must contain ANY of the selected people
                subquery = (
                    db.session.query(MediaItem.id)
                    .join(MediaItem.faces)
                    .join(Face.person_face)
                    .filter(PersonFace.person_id.in_(person_ids))
                    .distinct()
                )
                query = query.filter(MediaItem.id.in_(subquery))

        if gender is not None:
            subquery = (
                db.session.query(MediaItem.id)
                .join(MediaItem.faces)
                .outerjoin(Face.person_face)
                .outerjoin(PersonFace.person)
                .filter(func.coalesce(Person.gender, Face.gender) == gender)
            )
            query = query.filter(MediaItem.id.in_(subquery))

        if labels_ids and labels_match_type and len(labels_ids) > 0:
            if labels_match_type == 'all':
                for label_id in labels_ids:
                    subquery = (
                        db.session.query(MediaItem.id)
                        .join(MediaItem.labels)
                        .filter(MediaLabel.label_id == label_id)
                    )
                    query = query.filter(MediaItem.id.in_(subquery))
            else:
                subquery = (
                    db.session.query(MediaItem.id)
                        .join(MediaItem.labels)
                        .filter(MediaLabel.label_id.in_(labels_ids)))
                query = query.filter(MediaItem.id.in_(subquery))

        if tag_name and tag_value:
            # Filter by specific tag name and value
            subquery = (
                db.session.query(MediaItem.id)
                .join(MediaItem.tags)
                .filter(Tag.tag_name == tag_name)
                .filter(Tag.tag_value == tag_value)
                .distinct()
            )
            query = query.filter(MediaItem.id.in_(subquery))
        elif tag_name:
            # Filter by tag name only (any value)
            subquery = (
                db.session.query(MediaItem.id)
                .join(MediaItem.tags)
                .filter(Tag.tag_name == tag_name)
                .distinct()
            )
            query = query.filter(MediaItem.id.in_(subquery))

        if location_names and location_match_type and len(location_names) > 0:
            if location_match_type == 'all':
                # For locations, 'all' doesn't make sense (a photo can only have one location)
                # So we treat it as 'any'
                query = query.filter(MediaItem.location_name.in_(location_names))
            else:
                # OR logic: Photo location must match ANY of the selected locations
                query = query.filter(MediaItem.location_name.in_(location_names))

        if proximity_lat is not None and proximity_lon is not None and proximity_distance:
            min_lat, max_lat, min_lon, max_lon = calculate_bounding_box(
                proximity_lat, proximity_lon, proximity_distance
            )
            query = query.filter(
                MediaItem.latitude.isnot(None),
                MediaItem.longitude.isnot(None),
                MediaItem.latitude >= min_lat,
                MediaItem.latitude <= max_lat,
                MediaItem.longitude >= min_lon,
                MediaItem.longitude <= max_lon
            )

        # Get total count of filtered results
        media_count = query.count()

        # Apply pagination
        offset = (page - 1) * filter_page_size
        media_items = query.limit(filter_page_size).offset(offset).all()


        # Get unique people from photos (for display in cards)
        for media_item in media_items:
            # Create a set of unique people across all faces in the photo
            media_item.people = list({
                person
                for face in media_item.faces
                for person in face.people
            })
            # Split the stored path into name + folder for the hover details
            file_path = Path(media_item.full_file_path) if media_item.full_file_path else None
            media_item.file_name = file_path.name if file_path else ""
            media_item.folder = str(file_path.parent) if file_path else ""

        # Get distinct tag names and location names
        distinct_tag_names = (
            db.session.query(Tag.tag_name)
            .distinct()
            .order_by(Tag.tag_name)
            .all()
        )
        tag_names_list = [tag[0] for tag in distinct_tag_names if tag[0]]

        distinct_locations = (
            db.session.query(MediaItem.location_name)
            .filter(MediaItem.location_name.isnot(None))
            .distinct()
            .order_by(MediaItem.location_name)
            .all()
        )
        location_names_list = [loc[0] for loc in distinct_locations if loc[0]]

        distinct_devices = (
            db.session.query(MediaItem.device)
            .filter(MediaItem.device.isnot(None))
            .filter(MediaItem.device != "")
            .distinct()
            .order_by(MediaItem.device)
            .all()
        )
        device_list = [d[0] for d in distinct_devices if d[0]]

        labels = (
            db.session.query(ClassificationLabel)
            .filter(ClassificationLabel.enabled == True)
            .order_by(func.lower(ClassificationLabel.name))
            .all()
        )

        # Prepare filter options
        filters = {
            'people': Person.query.order_by(Person.name).all(),
            'years': get_distinct_years(db.session),
            'months': get_distinct_months(),
            'tag_names': tag_names_list,
            'location_names': location_names_list,
            'devices': device_list,
            'labels': labels,
            'genders': [
                {'name': gettext("Male"), 'value': 1},
                {'name': gettext("Female"), 'value': 0},
            ],
            'selected_path': path,
            'selected_person_ids': person_ids,
            'selected_person_match_type': person_match_type,
            'selected_label_ids':labels_ids,
            'selected_labels_match_type': labels_match_type,
            'selected_tag_name': tag_name,
            'selected_tag_value': tag_value,
            'selected_location_names': location_names,
            'selected_location_match_type': location_match_type,
            'selected_proximity_lat': proximity_lat,
            'selected_proximity_lon': proximity_lon,
            'selected_proximity_distance': proximity_distance,
            'selected_proximity_location': proximity_location,
            'selected_year': year,
            'selected_month': month,
            'selected_device': device,
            'selected_favorite': favorite,
            'selected_media_type': media_type,
            'selected_gender': gender,
            "page_sizes": [10, 25, 50, 100, 250],
            "page_size": filter_page_size
        }

        pagination = {
            "current_page": page,
            "total_items": media_count,
            "page_size": filter_page_size,
            "page_sizes": [10, 25, 50, 100, 250],
        }

        return render_template(
            "index.html",
            media_items=media_items,
            filters=filters,
            media_count=media_count,
            pagination=pagination,
            filter_layout=filter_config.load_layout(db.session),
            filter_default_keys=filter_config.default_keys(),
        )

    @app.route("/settings/home-filters", methods=["POST"])
    def save_home_filters():
        """Persist the gallery sidebar's filter layout (order + visibility). Scoped to
        this page's filter set; body is {"items": [{key, visible}, ...]}."""
        payload = request.get_json(silent=True) or {}
        items = payload.get("items")
        if not isinstance(items, list):
            return {
                "error": gettext("Items must be a list"),
                "code": "items_must_be_list",
            }, 400
        filter_config.save_layout(db.session, items)
        return "", 204

    @app.route("/api/tag-values", methods=["GET"])
    def get_tag_values():
        """
        API endpoint to get distinct tag values for a given tag name.
        Query params: tag_name
        """
        tag_name = request.args.get("tag_name")
        if not tag_name:
            return jsonify({
                "error": gettext("The tag_name parameter is required"),
                "code": "tag_name_required",
            }), 400

        distinct_values = (
            db.session.query(Tag.tag_value)
            .filter(Tag.tag_name == tag_name)
            .filter(Tag.tag_value.isnot(None))
            .distinct()
            .order_by(Tag.tag_value)
            .all()
        )

        values = [val[0] for val in distinct_values if val[0]]
        return jsonify({"tag_name": tag_name, "values": values})

    @app.route("/api/location-autocomplete", methods=["GET"])
    def location_autocomplete():
        """
        API endpoint for location autocomplete with geocoding.
        Combines results from:
        1. Existing photo locations in database
        2. OpenStreetMap Nominatim geocoding
        Query params: q (search query)
        """
        query = request.args.get("q", "").strip()
        if not query or len(query) < 2:
            return jsonify({"results": []})

        results = []

        db_locations = (
            db.session.query(MediaItem.location_name, MediaItem.latitude, MediaItem.longitude)
            .filter(MediaItem.location_name.isnot(None))
            .filter(MediaItem.location_name.ilike(f"%{query}%"))
            .limit(5)
            .all()
        )

        for photos_by_name in py_.group_by(
            db_locations,
            lambda media_item: media_item.location_name,
        ).values():
            lat = py_.sum_by(
                photos_by_name,
                lambda media_item: media_item.latitude,
            ) / len(photos_by_name)
            lon = py_.sum_by(
                photos_by_name,
                lambda media_item: media_item.longitude,
            ) / len(photos_by_name)
            if lat is not None and lon is not None:
                results.append({
                    "name": photos_by_name[0].location_name,
                    "lat": lat,
                    "lon": lon,
                    "source": "photos"
                })

        try:
            osm_response = requests.get(
                "https://nominatim.openstreetmap.org/search",
                params={
                    "q": query,
                    "format": "json",
                    "limit": 5
                },
                headers={
                    "User-Agent": "PhotoOrganizer/1.0"
                },
                timeout=3
            )

            if osm_response.status_code == 200:
                osm_data = osm_response.json()
                for item in osm_data:
                    results.append({
                        "name": item.get("display_name"),
                        "lat": float(item.get("lat")),
                        "lon": float(item.get("lon")),
                        "source": "osm"
                    })
        except requests.RequestException:
            pass

        return jsonify({"results": results})
