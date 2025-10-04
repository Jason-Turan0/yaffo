from flask import Flask, render_template, request, redirect, url_for, flash, session
from sqlalchemy import func, extract
from sqlalchemy.orm import joinedload, aliased

from photo_organizer.db import db
from photo_organizer.db.models import Person, PersonFace, Face, FACE_STATUS_UNASSIGNED, Photo
from photo_organizer.db.repositories.person_repository import update_person_embedding
from photo_organizer.db.repositories.photos_repository import get_distinct_months
from photo_organizer.routes.home import get_distinct_years

DEFAULT_THRESHOLD = 0.95  # configurable similarity threshold
FACE_LOAD_LIMIT = 250

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

        # Delete all PersonFace associations
        PersonFace.query.filter(PersonFace.person_id == person_id).delete()

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
        selected_similarity = request.args.get("similarity", type=float)
        face_page_size = request.args.get("face-page-size", type=int)
        filter_face_page_size = face_page_size if face_page_size else FACE_LOAD_LIMIT

        if not person:
            flash("Person not found", "error")
            return redirect(url_for("people_list"))
        PhotoAlias = aliased(Photo)
        # Get all faces for this person
        query = (
            db.session.query(Face)
            .join(PersonFace)
            .join(PhotoAlias, Face.photo)
            .filter(PersonFace.person_id == person_id)
            .options(joinedload(Face.photo))
        )
        if year:
            query = query.filter(extract("year", Photo.date_taken) == year)
        if month:
            query = query.filter(extract("month", Photo.date_taken) == month)
        if selected_similarity and selected_similarity > 0:
            query = query.filter(PersonFace.similarity > selected_similarity)
        faces = (query
                 .order_by(PhotoAlias.date_taken)
                 .limit(filter_face_page_size)
                 .all())

        filters = {
            "years": get_distinct_years(db.session),
            "selected_year": year,
            "months": get_distinct_months(),
            "selected_month": month,
            "face_page_sizes": [50, 100, 250, 500, 1000],
            "face_page_size": filter_face_page_size,
            "selected_similarity": selected_similarity,
        }
        face_data = [{"face": face, "similarity": face.person_face.similarity } for face in faces]
        return render_template("people/faces.html", person=person, faces=face_data, filters=filters)

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