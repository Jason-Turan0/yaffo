from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify
from sqlalchemy import func
from sqlalchemy.orm import joinedload, aliased

from yaffo.db import db
from yaffo.db.models import Person, PersonFace, Face, FACE_STATUS_UNASSIGNED, Photo
from yaffo.db.repositories.person_repository import update_person_embedding
from yaffo.db.repositories.photos_repository import get_distinct_months, get_distinct_years
from yaffo.utils.context import context

DEFAULT_THRESHOLD = 0.95  # configurable similarity threshold
FACE_LOAD_LIMIT = 250
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
                func.count(func.distinct(Face.photo_id)).label('num_photos')
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
            flash("Name is required", "error")
            return redirect(url_for("people_list"))

        # Check if person already exists
        existing = Person.query.filter(Person.name == name).first()
        if existing:
            flash(f"Person '{name}' already exists", "error")
            return redirect(url_for("people_list"))

        person = Person(name=name)
        db.session.add(person)
        db.session.commit()

        flash(f"Added {name}", "success")
        return redirect(url_for("people_list"))

    @app.route("/api/people/create", methods=["POST"])
    def api_people_create():
        """Create a new person via JSON API"""
        data = request.get_json()
        name = data.get("name", "").strip()

        if not name:
            return jsonify({"error": "Name is required"}), 400

        existing = Person.query.filter(Person.name == name).first()
        if existing:
            return jsonify({"error": f"Person '{name}' already exists"}), 400

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
            flash("Person not found", "error")
            return redirect(url_for("people_list"))

        name = request.form.get("name", "").strip()
        if not name:
            flash("Name is required", "error")
            return redirect(url_for("people_list"))

        # Check if new name conflicts with another person
        existing = Person.query.filter(
            Person.name == name,
            Person.id != person_id
        ).first()
        if existing:
            flash(f"Person '{name}' already exists", "error")
            return redirect(url_for("people_list"))

        old_name = person.name
        person.name = name
        db.session.commit()

        flash(f"Renamed '{old_name}' to '{name}'", "success")
        return redirect(url_for("people_list"))

    @app.route("/people/<int:person_id>/delete", methods=["POST"])
    def people_delete(person_id):
        """Delete a person and unassign all their faces"""
        person = db.session.get(Person, person_id)
        if not person:
            flash("Person not found", "error")
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

        flash(f"Deleted {name}", "success")
        return redirect(url_for("people_list"))

    @app.route("/people/<int:person_id>/faces", methods=["GET"])
    def person_faces(person_id):
        """View all faces for a specific person"""
        person = db.session.get(Person, person_id)
        year = request.args.get("year", type=int)
        month = request.args.get("month", type=int)
        min_similarity = request.args.get("min_similarity", type=float)
        max_similarity = request.args.get("max_similarity", type=float)
        page = request.args.get("page", default=1, type=int)
        page_size = request.args.get("page-size", type=int)
        filter_face_page_size = page_size if page_size else FACE_LOAD_LIMIT

        if not person:
            flash("Person not found", "error")
            return redirect(url_for("people_list"))

        photo_alias = aliased(Photo)

        # Build base query for this person
        query = (
            db.session.query(Face)
            .join(PersonFace)
            .join(photo_alias, Face.photo)
            .filter(PersonFace.person_id == person_id)
            .options(
                joinedload(Face.photo),  # eager load photo
                joinedload(Face.person_face)  # eager load person_face
            )
        )

        if year:
            query = query.filter(photo_alias.year == year)
        if month:
            query = query.filter(photo_alias.month == month)
        if min_similarity and min_similarity > 0:
            query = query.filter(PersonFace.similarity > min_similarity)
        if max_similarity and max_similarity > 0:
            query = query.filter(PersonFace.similarity < max_similarity)

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
            {"face": face, "similarity": face.person_face.similarity}
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
            flash("Person not found", "error")
            return redirect(request.referrer or url_for("faces_index"))
        selected_face_ids = request.form.getlist("faces")
        if not selected_face_ids or len(selected_face_ids) == 0:
            flash("No faces selected", "error")
            return redirect(request.referrer or url_for("faces_index"))

        if selected_face_ids:
            # Convert to ints
            face_ids = [int(fid) for fid in selected_face_ids]

            # Step 1: delete from bridge table (PersonFace)
            PersonFace.query.filter(PersonFace.face_id.in_(face_ids)).delete(synchronize_session=False)

            # Step 2: update statuses of the faces
            db.session.query(Face).filter(Face.id.in_(face_ids)).update(
                {Face.status: FACE_STATUS_UNASSIGNED},
                synchronize_session=False
            )
            db.session.commit()
        flash("Person updated", "success")
        update_person_embedding(person_id, db.session)
        return redirect(request.referrer or url_for("faces_index"))