import calendar
from dataclasses import dataclass
from typing import Optional, Tuple, List
import numpy as np
from flask import Flask, render_template, request, redirect, url_for, jsonify
from joblib.externals.loky.backend.reduction import DEFAULT_ENV
from sqlalchemy import extract
from sqlalchemy.dialects.sqlite import insert
import pydash as _
from sqlalchemy.orm import joinedload

from photo_organizer.db.models import db, Face, Person, PersonFace, FACE_STATUS_UNASSIGNED, FACE_STATUS_IGNORED, \
    FACE_STATUS_ASSIGNED, Photo, PersonEmbedding
from sklearn.metrics.pairwise import cosine_similarity

from photo_organizer.db.repositories.person_repository import update_person_embedding
from photo_organizer.db.repositories.photos_repository import get_distinct_years, get_distinct_months
from photo_organizer.domain.compare_utils import load_embedding, calculate_similarity

DEFAULT_THRESHOLD = 0.95  # configurable similarity threshold
FACE_LOAD_LIMIT = 250


@dataclass
class FaceViewModel:
    id: int
    relative_file_path: str
    photo_date: str
    similarity: Optional[float]

@dataclass
class FaceSuggestion:
    person_ids: list[int]
    people: list[Person]
    suggestion_name: str
    faces: list[FaceViewModel]


def init_faces_routes(app: Flask):
    @app.route("/faces", methods=["GET"])
    def faces_index():
        query = (
                db.session.query(Face)
                    .join(Face.photo)
                    .options(joinedload(Face.photo))
                    .outerjoin(Face.people)
                 )
        year = request.args.get("year", type=int)
        month = request.args.get("month", type=int)
        threshold = request.args.get("threshold", default=DEFAULT_THRESHOLD, type=float)
        page_size = request.args.get("page-size", default=FACE_LOAD_LIMIT, type=int)
        person_id = request.args.get("person", type=int)

        if year:
            query = query.filter(extract("year", Photo.date_taken) == year)
        if month:
            query = query.filter(extract("month", Photo.date_taken) == month)
        query = query.filter(Face.status == FACE_STATUS_UNASSIGNED)
        unassigned_faces :List[Face] = query.limit(page_size).all()
        unassigned_face_count = db.session.query(Face).filter(Face.status == FACE_STATUS_UNASSIGNED).count()

        people = (db.session.query(Person)
                  .options(joinedload(Person.embeddings_by_year))
                  .order_by(Person.name)
                  .all()
                  )
        face_suggestions = []
        default_suggestion = FaceSuggestion(
            person_ids=[],
            people=[],
            suggestion_name='Unknown',
            faces=[]
        )
        for face in unassigned_faces:
            emb = load_embedding(face.embedding)

            def flat_map_people(person: Person) -> List[Tuple[Person, int, np.ndarray]]:
                return [(person, embedding_by_year.year, load_embedding(embedding_by_year.avg_embedding))
                        for embedding_by_year in person.embeddings_by_year]

            matching_people : List[Tuple[Person, float]] = (
                _.chain(people)
                 .flat_map(flat_map_people)
                 .map(lambda tuple: (tuple[0], tuple[1], cosine_similarity([emb], [tuple[2]])[0][0]))
                 .filter(lambda tuple: tuple[2] > threshold and face.photo.date_taken[:4] == str(tuple[1]) and (person_id is None or tuple[0].id == person_id))
                 .sort_by(lambda pair: pair[1], True)
                 .group_by(lambda pair: pair[0].id)
                 .values()
                 .map(lambda tuples_by_person: tuples_by_person[0])
                 .value()
            )
            best_suggestion: FaceSuggestion | None = next(
                (suggestion for suggestion in face_suggestions
                if set(suggestion.person_ids) == (set([pair[0].id for pair in matching_people]))), None
            )
            if best_suggestion is None and len(matching_people) > 0:
                best_suggestion = FaceSuggestion(
                    person_ids = [pair[0].id for pair in matching_people],
                    people = [pair[0] for pair in matching_people],
                    suggestion_name= " OR ".join([pair[0].name for pair in matching_people] ),
                    faces=[]
                )
                face_suggestions.append(best_suggestion)

            if best_suggestion is not None:
                best_sim = matching_people[0][1]
                best_suggestion.faces.append(FaceViewModel(face.id, face.relative_file_path,face.photo.date_taken, best_sim ))
            else:
                default_suggestion.faces.append(FaceViewModel(face.id, face.relative_file_path, face.photo.date_taken, None))

        face_suggestions.append(default_suggestion)
        for suggestion in face_suggestions:
            suggestion.faces = _.sort_by(suggestion.faces, lambda f: f.similarity if f.similarity is not None else 0, reverse=True)
        months = get_distinct_months()
        years = get_distinct_years(db.session)
        filters = {
            "years":years,
            "selected_year": year,
            "months": months,
            "selected_month": month,
            "selected_threshold": threshold,
            "page_sizes": [50,100,250,500,1000],
            "page_size": page_size,
            "people": people,
            'selected_person_id': person_id,
        }
        return render_template(
            "faces/index.html", faces=unassigned_faces, people=people, face_suggestions=face_suggestions, filters=filters, unassigned_face_count=unassigned_face_count
        )

    @app.route("/faces/assign", methods=["POST"])
    def faces_assign():
        # Check if this is an async request (JSON expected)
        is_async = request.headers.get('X-Requested-With') == 'XMLHttpRequest'

        # Get selected face IDs from form/JSON
        if is_async:
            data = request.get_json()
            selected_face_ids = data.get("faces", [])
            person_id = data.get("person")
            face_status = data.get("face_status")
        else:
            selected_face_ids = request.form.getlist("faces")
            person_id = request.form.get("person")
            face_status = request.form.get("face_status")

        try:
            if face_status == FACE_STATUS_IGNORED:
                db.session.query(Face).filter(Face.id.in_(selected_face_ids)).update(
                    {Face.status: face_status}, synchronize_session=False
                )
                db.session.commit()

                if is_async:
                    return jsonify({
                        "success": True,
                        "message": f"Successfully ignored {len(selected_face_ids)} face(s)",
                        "face_ids": selected_face_ids
                    })

            elif selected_face_ids and person_id and face_status == FACE_STATUS_ASSIGNED:
                person : Person | None = (Person.query.options(joinedload(Person.embeddings_by_year)).order_by(Person.name).get(int(person_id)))
                if person is None:
                    error_msg = f'Person {person_id} not found'
                    print(error_msg)
                    if is_async:
                        return jsonify({"success": False, "message": error_msg}), 404
                    return redirect(request.referrer or url_for("faces_index"))

                faces = (Face.query.filter(Face.id.in_(selected_face_ids)))
                similarity_by_face_id = calculate_similarity(person, faces)

                stmt = insert(PersonFace).values([
                    {"person_id": person_id, "face_id": fid, "similarity": similarity_by_face_id.get(int(fid))}
                    for fid in selected_face_ids
                ])
                stmt = stmt.on_conflict_do_nothing(
                    index_elements=["person_id", "face_id"]
                )
                db.session.execute(stmt)
                db.session.query(Face).filter(Face.id.in_(selected_face_ids)).update(
                    {Face.status: face_status}, synchronize_session=False
                )
                db.session.commit()
                update_person_embedding(person_id, db.session)

                if is_async:
                    return jsonify({
                        "success": True,
                        "message": f"Successfully assigned {len(selected_face_ids)} face(s) to {person.name}",
                        "face_ids": selected_face_ids
                    })

        except Exception as e:
            db.session.rollback()
            error_msg = f"Error processing faces: {str(e)}"
            print(error_msg)
            if is_async:
                return jsonify({"success": False, "message": error_msg}), 500

        return redirect(request.referrer or url_for("faces_index"))