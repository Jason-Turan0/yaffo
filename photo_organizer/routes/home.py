import calendar
from flask import Flask, render_template, request, jsonify
from sqlalchemy import extract, distinct, func
from sqlalchemy.orm import joinedload

from photo_organizer.db import db
from photo_organizer.db.models import Photo, Face, Person, PersonFace, Tag
from photo_organizer.db.repositories.photos_repository import get_distinct_years, get_distinct_months


def init_home_routes(app: Flask):
    @app.route("/", methods=["GET"])
    def index():
        # Get filter parameters
        person_ids = request.args.getlist("person", type=int)
        person_match_type = request.args.get("person-match-type", default='any', type=str)
        tag_name = request.args.get("tag-name", type=str)
        tag_value = request.args.get("tag-value", type=str)
        location_names = request.args.getlist("location", type=str)
        location_match_type = request.args.get("location-match-type", default='any', type=str)
        year = request.args.get("year", type=int)
        month = request.args.get("month", type=int)
        page_size = request.args.get("page-size", type=int)
        filter_page_size = page_size if page_size else 100
        # Build query with eager loading
        query = (
            db.session.query(Photo)
            .options(joinedload(Photo.faces).joinedload(Face.people))
            .order_by(Photo.date_taken.desc())
        )

        # Apply filters
        if year:
            query = query.filter(extract("year", Photo.date_taken) == year)
        if month:
            query = query.filter(extract("month", Photo.date_taken) == month)
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

        photos = query.limit(filter_page_size).all()
        photo_count = db.session.query(func.count(Photo.id)).scalar()


        # Get unique people from photos (for display in cards)
        for photo in photos:
            # Create a set of unique people across all faces in the photo
            photo.people = list({
                person
                for face in photo.faces
                for person in face.people
            })

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

        # Prepare filter options
        filters = {
            'people': Person.query.order_by(Person.name).all(),
            'years': get_distinct_years(db.session),
            'months': get_distinct_months(),
            'tag_names': tag_names_list,
            'location_names': location_names_list,
            'selected_person_ids': person_ids,
            'selected_person_match_type': person_match_type,
            'selected_tag_name': tag_name,
            'selected_tag_value': tag_value,
            'selected_location_names': location_names,
            'selected_location_match_type': location_match_type,
            'selected_year': year,
            'selected_month': month,
            "page_sizes": [50, 100, 250, 500, 1000],
            "page_size": filter_page_size
        }

        return render_template("index.html", photos=photos, filters=filters, photo_count=photo_count)

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



