import calendar
from flask import Flask, render_template, request
from sqlalchemy import extract, distinct, func
from sqlalchemy.orm import joinedload

from photo_organizer.db import db
from photo_organizer.db.models import Photo, Face, Person, PersonFace
from photo_organizer.db.repositories.photos_repository import get_distinct_years, get_distinct_months


def init_home_routes(app: Flask):
    @app.route("/", methods=["GET"])
    def index():
        # Get filter parameters
        person_ids = request.args.getlist("person", type=int)
        person_match_type = request.args.get("person-match-type", default='any', type=str)
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

        # Prepare filter options
        filters = {
            'people': Person.query.order_by(Person.name).all(),
            'years': get_distinct_years(db.session),
            'months': get_distinct_months(),
            'selected_person_ids': person_ids,
            'selected_person_match_type': person_match_type,
            'selected_year': year,
            'selected_month': month,
            "page_sizes": [50, 100, 250, 500, 1000],
            "page_size": filter_page_size
        }

        return render_template("index.html", photos=photos, filters=filters, photo_count=photo_count)



