from pathlib import Path

import pydash as py_
import requests
from flask import Flask, jsonify, render_template, request
from flask_babel import gettext
from sqlalchemy.orm import joinedload

from yaffo.db import db
from yaffo.db.models import (
    Face,
    MediaItem,
    MediaLabel,
    Tag,
)
from yaffo.db.repositories.media_filter_repository import apply_media_filters
from yaffo.routes import filter_config
from yaffo.routes.filter_panel import build_filters_context, to_media_filters, to_query_params
from yaffo.utils.context import context


@context("yaffo-gallery")
def init_home_routes(app: Flask):
    @app.route("/", methods=["GET"])
    def index():
        # Parse filter parameters + build the sidebar's option lists (shared with
        # the locations page, which filters client-side from the same panel).
        filters = build_filters_context(db.session, request.args)
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

        # Apply filters (semantics shared with the p2p sharing handler —
        # see media_filter_repository, and with the album add screen via
        # to_media_filters).
        query = apply_media_filters(db.session, query, to_media_filters(filters))

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
            filter_params=to_query_params(filters),
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
