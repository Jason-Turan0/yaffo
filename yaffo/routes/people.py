from collections import Counter
from datetime import date

from flask import Flask, render_template, request, redirect, url_for, flash
from flask_babel import gettext, ngettext
from sqlalchemy import func
from sqlalchemy.orm import joinedload, aliased

from yaffo.db import db
from yaffo.db.models import Person, PersonFace, Face, MediaItem, EVENT_MEDIA_MODIFIED
from yaffo.db.repositories.person_repository import (
    clear_faces,
    delete_person,
    get_media_item_ids_for_person,
    get_similarity_bounds,
    likely_misassigned_faces,
    update_person_embedding,
)
from yaffo.db.repositories.media_repository import get_distinct_months, get_distinct_years
from yaffo.domain.compare_utils import ui_threshold_to_similarity, similarity_to_ui_percent
from yaffo.background_tasks.events import emit_event
from yaffo.background_tasks.tasks.assign_faces_to_person import assign_faces_to_person_now
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
        # Faces go back to unassigned; shared with the assistant's delete_person.
        delete_person(db.session, person_id)

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
        misassigned = request.args.get("misassigned") == "1"

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

        offset = (page - 1) * filter_face_page_size
        matches_by_face_id = {}
        if misassigned:
            # Faces that look more like someone else, biggest gap first. The check
            # scores every matching face, so it runs over the filtered ids and the
            # page is cut from its ordered result.
            matches = likely_misassigned_faces(
                db.session, person_id, [face_id for (face_id,) in query.with_entities(Face.id).all()])
            total_faces = len(matches)
            page_matches = matches[offset:offset + filter_face_page_size]
            matches_by_face_id = {match.face_id: match for match in page_matches}
            faces_by_id = {face.id: face for face in query.filter(Face.id.in_(list(matches_by_face_id))).all()}
            faces = [faces_by_id[match.face_id] for match in page_matches if match.face_id in faces_by_id]
            # Offer the person most of these look like as the move target.
            suggested_person_id = (
                Counter(match.other_person_id for match in matches).most_common(1)[0][0] if matches else None)
        else:
            total_faces = query.count()
            faces = (
                query
                .order_by(PersonFace.similarity)
                .limit(filter_face_page_size)
                .offset(offset)
                .all()
            )
            suggested_person_id = None

        filters = {
            "years": get_distinct_years(db.session),
            "selected_year": year,
            "months": get_distinct_months(),
            "selected_month": month,
            "page_sizes": [50, 100, 250, 500, 1000],
            "page_size": filter_face_page_size,
            "min_similarity": min_similarity,
            "max_similarity": max_similarity,
            "misassigned": misassigned,
        }

        def face_entry(face: Face) -> dict:
            match = matches_by_face_id.get(face.id)
            # In the misassigned view the score is the freshly computed one the
            # comparison used, so the two numbers on the card agree.
            similarity = match.similarity if match else face.person_face.similarity
            return {
                "face": face,
                "similarity": similarity,
                "similarity_pct": similarity_to_ui_percent(similarity, *bounds) if similarity is not None else None,
                "closer_to": {
                    "person_id": match.other_person_id,
                    "name": match.other_person_name,
                    "similarity_pct": similarity_to_ui_percent(match.other_similarity, *bounds),
                } if match else None,
            }

        face_data = [face_entry(face) for face in faces]
        # Everyone but this person, as targets for "Move Selected to".
        other_people = (
            db.session.query(Person).filter(Person.id != person_id).order_by(Person.name).all()
        )

        pagination = {
            "current_page": page,
            "total_items": total_faces,
            "page_size": filter_face_page_size,
            "page_sizes": [50, 100, 250, 500, 1000],
        }

        return render_template("people/faces.html", person=person, faces=face_data, filters=filters,
                               pagination=pagination, other_people=other_people,
                               suggested_person_id=suggested_person_id)

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

        cleared = clear_faces(db.session, [int(fid) for fid in selected_face_ids])
        if cleared.media_item_ids:
            emit_event(EVENT_MEDIA_MODIFIED, {"media_item_ids": cleared.media_item_ids})
        flash(gettext("Person updated"), "success")
        return redirect(request.referrer or url_for("faces_index"))

    @app.route("/people/<int:person_id>/faces/move", methods=["POST"])
    def person_faces_move(person_id):
        """Reassign selected faces from this person to another, e.g. faces the
        "likely misassigned" filter shows look more like a sibling. Both people's
        galleries are rebuilt: the target's by the assignment, this person's here."""
        back = request.referrer or url_for("person_faces", person_id=person_id)
        person = db.session.get(Person, person_id)
        if not person:
            flash(gettext("Person not found"), "error")
            return redirect(url_for("people_list"))
        target = db.session.get(Person, request.form.get("target_person", type=int) or 0)
        if target is None or target.id == person_id:
            flash(gettext("Choose another person to move the faces to"), "error")
            return redirect(back)
        try:
            requested = {int(face_id) for face_id in request.form.getlist("faces")}
        except ValueError:
            requested = set()
        # Only faces that really are this person's move; anything else is ignored.
        face_ids = sorted(
            face_id for (face_id,) in db.session.query(PersonFace.face_id)
            .filter(PersonFace.person_id == person_id, PersonFace.face_id.in_(requested))
        ) if requested else []
        if not face_ids:
            flash(gettext("No faces selected"), "error")
            return redirect(back)

        assign_faces_to_person_now(db.session, target.id, face_ids)
        update_person_embedding(person_id, db.session)
        flash(ngettext(
            "Moved %(count)s face to %(person)s",
            "Moved %(count)s faces to %(person)s",
            len(face_ids), count=len(face_ids), person=target.name,
        ), "success")
        return redirect(back)
