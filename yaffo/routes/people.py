from datetime import date

from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify
from flask_babel import gettext
from sqlalchemy import func
from sqlalchemy.orm import joinedload, aliased

from yaffo.db import db
from yaffo.db.models import Person, PersonFace, Face, FACE_STATUS_UNASSIGNED, MediaItem, EVENT_MEDIA_MODIFIED
from yaffo.db.repositories.person_repository import update_person_embedding, get_media_item_ids_for_person, get_similarity_bounds
from yaffo.db.repositories.media_repository import get_distinct_months, get_distinct_years
from yaffo.domain.compare_utils import ui_threshold_to_similarity, similarity_to_ui_percent
from yaffo.background_tasks.events import emit_event
from yaffo.utils.context import context

DEFAULT_THRESHOLD = 0.95  # configurable similarity threshold
FACE_LOAD_LIMIT = 250


def _parse_optional_gender(raw_gender: str | None) -> int | None:
    value = (raw_gender or "").strip()
    if not value:
        return None
    if value not in {"0", "1"}:
        raise ValueError(gettext("Gender must be male, female, or not specified"))
    return int(value)


@context("yaffo-face_assignment")
def init_people_routes(app: Flask):
    @app.route("/people", methods=["GET"])
    def people_list():
        """List all people with face and photo counts"""
        # Query people with aggregated counts
        people = (
            db.session.query(
                Person,
                func.count(func.distinct(PersonFace.face_id)).label('num_faces'),
                func.count(func.distinct(Face.media_item_id)).label('num_photos')
            )
            .outerjoin(PersonFace, Person.id == PersonFace.person_id)
            .outerjoin(Face, PersonFace.face_id == Face.id)
            .group_by(Person.id)
            .order_by(Person.name)
            .all()
        )

        # Convert to list of objects with counts
        people_list = []
        for person, num_faces, num_photos in people:
            person.num_faces = num_faces or 0
            person.num_photos = num_photos or 0
            people_list.append(person)

        return render_template("people/list.html", people=people_list)

    @app.route("/people/create", methods=["POST"])
    def people_create():
        """Create a new person"""
        name = request.form.get("name", "").strip()
        if not name:
            flash(gettext("Name is required"), "error")
            return redirect(url_for("people_list"))

        # Check if person already exists
        existing = Person.query.filter(Person.name == name).first()
        if existing:
            flash(gettext("Person '%(name)s' already exists", name=name), "error")
            return redirect(url_for("people_list"))

        try:
            gender = _parse_optional_gender(request.form.get("gender"))
        except ValueError as error:
            flash(str(error), "error")
            return redirect(url_for("people_list"))

        person = Person(name=name, gender=gender)
        db.session.add(person)
        db.session.commit()

        flash(gettext("Added %(name)s", name=name), "success")
        return redirect(url_for("people_list"))

    @app.route("/api/people/create", methods=["POST"])
    def api_people_create():
        """Create a new person via JSON API"""
        data = request.get_json(silent=True) or {}
        raw_name = data.get("name")
        name = raw_name.strip() if isinstance(raw_name, str) else ""

        if not name:
            return jsonify({
                "error": gettext("Name is required"),
                "code": "name_required",
            }), 400

        existing = Person.query.filter(Person.name == name).first()
        if existing:
            return jsonify({
                "error": gettext("Person '%(name)s' already exists", name=name),
                "code": "person_already_exists",
            }), 400

        person = Person(name=name)
        db.session.add(person)
        db.session.commit()

        return jsonify({
            "success": True,
            "person_id": person.id,
            "name": person.name
        }), 201

    @app.route("/people/<int:person_id>/update", methods=["POST"])
    def people_update(person_id):
        """Update a person's name"""
        person = db.session.get(Person, person_id)
        if not person:
            flash(gettext("Person not found"), "error")
            return redirect(url_for("people_list"))

        name = request.form.get("name", "").strip()
        if not name:
            flash(gettext("Name is required"), "error")
            return redirect(url_for("people_list"))

        # Check if new name conflicts with another person
        existing = Person.query.filter(
            Person.name == name,
            Person.id != person_id
        ).first()
        if existing:
            flash(gettext("Person '%(name)s' already exists", name=name), "error")
            return redirect(url_for("people_list"))

        try:
            gender = _parse_optional_gender(request.form.get("gender"))
        except ValueError as error:
            flash(str(error), "error")
            return redirect(url_for("people_list"))

        old_name = person.name
        person.name = name
        person.gender = gender

        # Birthdate drives life-stage bucketing; a change re-buckets the gallery.
        raw_birthdate = (request.form.get("birthdate") or "").strip()
        old_birthdate = person.birthdate
        if raw_birthdate:
            try:
                person.birthdate = date.fromisoformat(raw_birthdate)
            except ValueError:
                flash(gettext("Birthdate must be YYYY-MM-DD"), "error")
                return redirect(url_for("people_list"))
        else:
            person.birthdate = None

        media_item_ids = get_media_item_ids_for_person(db.session, person_id)
        db.session.commit()

        if person.birthdate != old_birthdate:
            update_person_embedding(person_id, db.session)
        if media_item_ids:
            emit_event(EVENT_MEDIA_MODIFIED, {"media_item_ids": media_item_ids})
        flash(
            gettext("Updated '%(name)s'", name=name)
            if name == old_name
            else gettext("Renamed '%(old_name)s' to '%(name)s'", old_name=old_name, name=name),
            "success",
        )
        return redirect(url_for("people_list"))

    @app.route("/people/<int:person_id>/delete", methods=["POST"])
    def people_delete(person_id):
        """Delete a person and unassign all their faces"""
        person = db.session.get(Person, person_id)
        if not person:
            flash(gettext("Person not found"), "error")
            return redirect(url_for("people_list"))

        name = person.name

        # Update face statuses back to unassigned
        face_ids = (
            db.session.query(PersonFace.face_id)
            .filter(PersonFace.person_id == person_id)
            .all()
        )
        if face_ids:
            Face.query.filter(Face.id.in_([fid for (fid,) in face_ids])).update(
                {Face.status: FACE_STATUS_UNASSIGNED},
                synchronize_session=False
            )

        # Delete all PersonFace associations
        PersonFace.query.filter(PersonFace.person_id == person_id).delete()



        # Delete the person
        db.session.delete(person)
        db.session.commit()

        flash(gettext("Deleted %(name)s", name=name), "success")
        return redirect(url_for("people_list"))

    @app.route("/people/<int:person_id>/faces", methods=["GET"])
    def person_faces(person_id):
        """View all faces for a specific person"""
        person = db.session.get(Person, person_id)
        year = request.args.get("year", type=int)
        month = request.args.get("month", type=int)
        min_similarity = request.args.get("min_similarity", type=int)
        max_similarity = request.args.get("max_similarity", type=int)
        page = request.args.get("page", default=1, type=int)
        page_size = request.args.get("page-size", type=int)
        filter_face_page_size = page_size if page_size else FACE_LOAD_LIMIT

        if not person:
            flash(gettext("Person not found"), "error")
            return redirect(url_for("people_list"))

        photo_alias = aliased(MediaItem)

        # Build base query for this person
        query = (
            db.session.query(Face)
            .join(PersonFace)
            .join(photo_alias, Face.media_item)
            .filter(PersonFace.person_id == person_id)
            .options(
                joinedload(Face.media_item),  # eager load photo
                joinedload(Face.person_face)  # eager load person_face
            )
        )

        if year:
            query = query.filter(photo_alias.year == year)
        if month:
            query = query.filter(photo_alias.month == month)
        # Sliders are 0-100 UI values; PersonFace.similarity is stored cosine. The
        # band the 0-100 scale maps onto is read from the live data, not hardcoded.
        bounds = get_similarity_bounds(db.session)
        if min_similarity and min_similarity > 0:
            query = query.filter(PersonFace.similarity > ui_threshold_to_similarity(min_similarity, *bounds))
        if max_similarity and max_similarity > 0:
            query = query.filter(PersonFace.similarity < ui_threshold_to_similarity(max_similarity, *bounds))

        # Get total count for pagination
        total_faces = query.count()

        # Apply pagination
        offset = (page - 1) * filter_face_page_size
        faces = (
            query
            .order_by(PersonFace.similarity)
            .limit(filter_face_page_size)
            .offset(offset)
            .all()
        )

        filters = {
            "years": get_distinct_years(db.session),
            "selected_year": year,
            "months": get_distinct_months(),
            "selected_month": month,
            "page_sizes": [50, 100, 250, 500, 1000],
            "page_size": filter_face_page_size,
            "min_similarity": min_similarity,
            "max_similarity": max_similarity,
        }

        face_data = [
            {
                "face": face,
                "similarity": face.person_face.similarity,
                "similarity_pct": (
                    similarity_to_ui_percent(face.person_face.similarity, *bounds)
                    if face.person_face.similarity is not None else None
                ),
            }
            for face in faces
        ]

        pagination = {
            "current_page": page,
            "total_items": total_faces,
            "page_size": filter_face_page_size,
            "page_sizes": [50, 100, 250, 500, 1000],
        }

        return render_template("people/faces.html", person=person, faces=face_data, filters=filters, pagination=pagination)

    @app.route("/people/<int:person_id>/faces/remove", methods=["POST"])
    def person_faces_remove(person_id):
        person = db.session.get(Person, person_id)
        if not person:
            flash(gettext("Person not found"), "error")
            return redirect(request.referrer or url_for("faces_index"))
        selected_face_ids = request.form.getlist("faces")
        if not selected_face_ids or len(selected_face_ids) == 0:
            flash(gettext("No faces selected"), "error")
            return redirect(request.referrer or url_for("faces_index"))

        if selected_face_ids:
            # Convert to ints
            face_ids = [int(fid) for fid in selected_face_ids]

            media_item_ids = [
                pid for (pid,) in db.session.query(Face.media_item_id)
                .filter(Face.id.in_(face_ids), Face.media_item_id.isnot(None))
                .distinct()
            ]

            # Step 1: delete from bridge table (PersonFace)
            PersonFace.query.filter(PersonFace.face_id.in_(face_ids)).delete(synchronize_session=False)

            # Step 2: update statuses of the faces
            db.session.query(Face).filter(Face.id.in_(face_ids)).update(
                {Face.status: FACE_STATUS_UNASSIGNED},
                synchronize_session=False
            )
            db.session.commit()
            if media_item_ids:
                emit_event(EVENT_MEDIA_MODIFIED, {"media_item_ids": media_item_ids})
        flash(gettext("Person updated"), "success")
        update_person_embedding(person_id, db.session)
        return redirect(request.referrer or url_for("faces_index"))
