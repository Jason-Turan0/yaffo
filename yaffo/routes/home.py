import math
from pathlib import Path

import requests
from flask import Flask, render_template, request, jsonify
from sqlalchemy import distinct, func
from sqlalchemy.orm import joinedload
import pydash as _
from yaffo.db import db
from yaffo.db.models import Photo, Face, Person, PersonFace, Tag, ClassificationLabel, PhotoLabel
from yaffo.db.repositories.photos_repository import get_distinct_years, get_distinct_months
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
        page = request.args.get("page", default=1, type=int)
        page_size = request.args.get("page-size", type=int)
        filter_page_size = page_size if page_size else 25
        # Build query with eager loading
        query = (
            db.session.query(Photo)
            .options(joinedload(Photo.faces).joinedload(Face.people))
            .options(joinedload(Photo.labels).joinedload(PhotoLabel.label))
            .order_by(Photo.date_taken.desc())
        )

        # Apply filters
        if path:
            # Partial, case-insensitive match on any part of the stored path
            # (folders or file name); autoescape so %/_ in the term stay literal.
            query = query.filter(Photo.full_file_path.icontains(path, autoescape=True))
        if year:
            query = query.filter(Photo.year == year)
        if month:
            query = query.filter(Photo.month == month)
        if device:
            query = query.filter(Photo.device == device)
        if person_ids and person_match_type and len(person_ids) > 0:
            if person_match_type == 'all':
                # AND logic: Photo must contain ALL selected people
                for person_id in person_ids:
                    subquery = (
                        db.session.query(Photo.id)
                        .join(Photo.faces)
                        .join(Face.person_face)
                        .filter(PersonFace.person_id == person_id)
                    )
                    query = query.filter(Photo.id.in_(subquery))
            else:
                # OR logic: Photo must contain ANY of the selected people
                subquery = (
                    db.session.query(Photo.id)
                    .join(Photo.faces)
                    .join(Face.person_face)
                    .filter(PersonFace.person_id.in_(person_ids))
                    .distinct()
                )
                query = query.filter(Photo.id.in_(subquery))

        if gender is not None:
            ##TODO capture actual gender from user and use that for deterministic instead of approximate filter
            subquery = (
                db.session.query(Photo.id)
                .join(Photo.faces)
                .filter(Face.gender == gender)
            )
            query = query.filter(Photo.id.in_(subquery))

        if labels_ids and labels_match_type and len(labels_ids) > 0:
            if labels_match_type == 'all':
                for label_id in labels_ids:
                    subquery = (
                        db.session.query(Photo.id)
                        .join(Photo.labels)
                        .filter(PhotoLabel.label_id == label_id)
                    )
                    query = query.filter(Photo.id.in_(subquery))
            else:
                subquery = (
                    db.session.query(Photo.id)
                        .join(Photo.labels)
                        .filter(PhotoLabel.label_id.in_(labels_ids)))
                query = query.filter(Photo.id.in_(subquery))

        if tag_name and tag_value:
            # Filter by specific tag name and value
            subquery = (
                db.session.query(Photo.id)
                .join(Photo.tags)
                .filter(Tag.tag_name == tag_name)
                .filter(Tag.tag_value == tag_value)
                .distinct()
            )
            query = query.filter(Photo.id.in_(subquery))
        elif tag_name:
            # Filter by tag name only (any value)
            subquery = (
                db.session.query(Photo.id)
                .join(Photo.tags)
                .filter(Tag.tag_name == tag_name)
                .distinct()
            )
            query = query.filter(Photo.id.in_(subquery))

        if location_names and location_match_type and len(location_names) > 0:
            if location_match_type == 'all':
                # For locations, 'all' doesn't make sense (a photo can only have one location)
                # So we treat it as 'any'
                query = query.filter(Photo.location_name.in_(location_names))
            else:
                # OR logic: Photo location must match ANY of the selected locations
                query = query.filter(Photo.location_name.in_(location_names))

        if proximity_lat is not None and proximity_lon is not None and proximity_distance:
            min_lat, max_lat, min_lon, max_lon = calculate_bounding_box(
                proximity_lat, proximity_lon, proximity_distance
            )
            query = query.filter(
                Photo.latitude.isnot(None),
                Photo.longitude.isnot(None),
                Photo.latitude >= min_lat,
                Photo.latitude <= max_lat,
                Photo.longitude >= min_lon,
                Photo.longitude <= max_lon
            )

        # Get total count of filtered results
        photo_count = query.count()

        # Apply pagination
        offset = (page - 1) * filter_page_size
        photos = query.limit(filter_page_size).offset(offset).all()


        # Get unique people from photos (for display in cards)
        for photo in photos:
            # Create a set of unique people across all faces in the photo
            photo.people = list({
                person
                for face in photo.faces
                for person in face.people
            })
            # Split the stored path into name + folder for the hover details
            file_path = Path(photo.full_file_path) if photo.full_file_path else None
            photo.file_name = file_path.name if file_path else ""
            photo.folder = str(file_path.parent) if file_path else ""

        # Get distinct tag names and location names
        distinct_tag_names = (
            db.session.query(Tag.tag_name)
            .distinct()
            .order_by(Tag.tag_name)
            .all()
        )
        tag_names_list = [tag[0] for tag in distinct_tag_names if tag[0]]

        distinct_locations = (
            db.session.query(Photo.location_name)
            .filter(Photo.location_name.isnot(None))
            .distinct()
            .order_by(Photo.location_name)
            .all()
        )
        location_names_list = [loc[0] for loc in distinct_locations if loc[0]]

        distinct_devices = (
            db.session.query(Photo.device)
            .filter(Photo.device.isnot(None))
            .filter(Photo.device != "")
            .distinct()
            .order_by(Photo.device)
            .all()
        )
        device_list = [d[0] for d in distinct_devices if d[0]]

        labels = (
            db.session.query(ClassificationLabel)
            .filter(ClassificationLabel.enabled == True)
            .order_by(ClassificationLabel.name)
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
            'genders': [{'name': "Male", 'value': 1},{'name': "Female", 'value': 0}],
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
            'selected_gender': gender,
            "page_sizes": [10, 25, 50, 100, 250],
            "page_size": filter_page_size
        }

        pagination = {
            "current_page": page,
            "total_items": photo_count,
            "page_size": filter_page_size,
            "page_sizes": [10, 25, 50, 100, 250],
        }

        return render_template("index.html", photos=photos, filters=filters, photo_count=photo_count, pagination=pagination)

    @app.route("/api/tag-values", methods=["GET"])
    def get_tag_values():
        """
        API endpoint to get distinct tag values for a given tag name.
        Query params: tag_name
        """
        tag_name = request.args.get("tag_name")
        if not tag_name:
            return jsonify({"error": "tag_name parameter is required"}), 400

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
            db.session.query(Photo.location_name, Photo.latitude, Photo.longitude)
            .filter(Photo.location_name.isnot(None))
            .filter(Photo.location_name.ilike(f"%{query}%"))
            .limit(5)
            .all()
        )

        for photos_by_name in _.group_by(db_locations, lambda photo: photo.location_name).values():
            lat = _.sum_by(photos_by_name, lambda photo: photo.latitude)/len(photos_by_name)
            lon = _.sum_by(photos_by_name, lambda photo: photo.longitude)/len(photos_by_name)
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


