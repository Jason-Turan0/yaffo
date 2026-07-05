import math
from pathlib import Path

import pydash as py_
import requests
from flask import Flask, jsonify, render_template, request
from flask_babel import gettext
from sqlalchemy import func
from sqlalchemy.orm import joinedload

from yaffo.db import db
from yaffo.db.models import (
    Face,
    MediaItem,
    MediaLabel,
    Person,
    PersonFace,
    Tag,
)
from yaffo.distance_units import distance_to_kilometers
from yaffo.routes import filter_config
from yaffo.routes.filter_panel import build_filters_context
from yaffo.utils.context import context


def calculate_bounding_box(lat: float, lon: float, distance_kilometers: float) -> tuple[float, float, float, float]:
    """
    Calculate bounding box coordinates for a given center point and distance.
    Returns (min_lat, max_lat, min_lon, max_lon)
    """
    lat_degree_kilometers = 111.0
    lon_degree_kilometers = abs(math.cos(math.radians(lat)) * 111.0)

    lat_offset = distance_kilometers / lat_degree_kilometers
    lon_offset = distance_kilometers / lon_degree_kilometers

    min_lat = lat - lat_offset
    max_lat = lat + lat_offset
    min_lon = lon - lon_offset
    max_lon = lon + lon_offset

    return (min_lat, max_lat, min_lon, max_lon)


@context("yaffo-gallery")
def init_home_routes(app: Flask):
    @app.route("/", methods=["GET"])
    def index():
        # Parse filter parameters + build the sidebar's option lists (shared with
        # the locations page, which filters client-side from the same panel).
        filters = build_filters_context(db.session, request.args)
        path = filters["selected_path"]
        person_ids = filters["selected_person_ids"]
        person_match_type = filters["selected_person_match_type"]
        gender = filters["selected_gender"]
        labels_ids = filters["selected_label_ids"]
        labels_match_type = filters["selected_labels_match_type"]
        tag_name = filters["selected_tag_name"]
        tag_value = filters["selected_tag_value"]
        location_names = filters["selected_location_names"]
        location_match_type = filters["selected_location_match_type"]
        unnamed = filters["selected_unnamed"]
        proximity_lat = filters["selected_proximity_lat"]
        proximity_lon = filters["selected_proximity_lon"]
        proximity_distance = filters["selected_proximity_distance"]
        distance_unit = filters["selected_distance_unit"]
        year = filters["selected_year"]
        month = filters["selected_month"]
        device = filters["selected_device"]
        favorite = filters["selected_favorite"]
        media_type = filters["selected_media_type"]
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

        if unnamed:
            # Only photos with no location name; NULL and "" both count as unnamed
            # (the locations map treats a falsy name the same way).
            query = query.filter(func.coalesce(MediaItem.location_name, "") == "")

        if proximity_lat is not None and proximity_lon is not None and proximity_distance:
            min_lat, max_lat, min_lon, max_lon = calculate_bounding_box(
                proximity_lat, proximity_lon, distance_to_kilometers(proximity_distance, distance_unit)
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

        filters["page_sizes"] = [10, 25, 50, 100, 250]
        filters["page_size"] = filter_page_size

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
