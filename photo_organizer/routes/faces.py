import threading
from dataclasses import dataclass
from typing import Optional, Tuple, List
import numpy as np
from flask import Flask, render_template, request, jsonify

from photo_organizer.background_tasks.tasks import update_person_embedding_task
from photo_organizer.logging_config import get_logger
from sqlalchemy import extract
from sqlalchemy.dialects.sqlite import insert
import pydash as _
from sqlalchemy.orm import joinedload
from photo_organizer.db.models import db, Face, Person, PersonFace, FACE_STATUS_UNASSIGNED, FACE_STATUS_IGNORED, \
    FACE_STATUS_ASSIGNED, Photo
from sklearn.metrics.pairwise import cosine_similarity

from photo_organizer.db.repositories.person_repository import update_person_embedding
from photo_organizer.db.repositories.photos_repository import get_distinct_years, get_distinct_months
from photo_organizer.domain.compare_utils import load_embedding, calculate_similarity

DEFAULT_THRESHOLD = 0.97  # configurable similarity threshold
DEFAULT_PAGE_SIZE = 250


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


logger = get_logger(__name__, 'webapp')


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
        page = request.args.get("page", default=1, type=int)
        page_size = request.args.get("page-size", default=DEFAULT_PAGE_SIZE, type=int)
        person_id = request.args.get("person", type=int)
        assign_person_id = request.args.get("assign_person", type=int)

        if year:
            query = query.filter(extract("year", Photo.date_taken) == year)
        if month:
            query = query.filter(extract("month", Photo.date_taken) == month)
        query = query.filter(Face.status == FACE_STATUS_UNASSIGNED)

        # Get total count before pagination
        unassigned_face_count = query.count()

        # Apply pagination
        offset = (page - 1) * page_size
        unassigned_faces: List[Face] = query.limit(page_size).offset(offset).all()

        # Get people sorted by face count (descending) for keyboard shortcuts
        from sqlalchemy import func
        people = (db.session.query(Person)
                  .outerjoin(PersonFace)
                  .group_by(Person.id)
                  .options(joinedload(Person.embeddings_by_year))
                  .order_by(func.count(PersonFace.face_id).desc(), Person.name)
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

            matching_people: List[Tuple[Person, float]] = (
                _.chain(people)
                .flat_map(flat_map_people)
                .map(lambda tuple: (tuple[0], tuple[1], cosine_similarity([emb], [tuple[2]])[0][0]))
                .filter(lambda tuple: tuple[2] > threshold and (person_id is None or tuple[0].id == person_id))
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
                    person_ids=[pair[0].id for pair in matching_people],
                    people=[pair[0] for pair in matching_people],
                    suggestion_name=" OR ".join([pair[0].name for pair in matching_people]),
                    faces=[]
                )
                face_suggestions.append(best_suggestion)

            if best_suggestion is not None:
                best_sim = matching_people[0][2]
                best_suggestion.faces.append(
                    FaceViewModel(face.id, face.relative_file_path, face.photo.date_taken, best_sim))
            else:
                default_suggestion.faces.append(
                    FaceViewModel(face.id, face.relative_file_path, face.photo.date_taken, None))

        face_suggestions.append(default_suggestion)
        for suggestion in face_suggestions:
            suggestion.faces = _.sort_by(suggestion.faces, lambda f: f.similarity if f.similarity is not None else 0,
                                         reverse=True)
        months = get_distinct_months()
        years = get_distinct_years(db.session)
        filters = {
            "years": years,
            "selected_year": year,
            "months": months,
            "selected_month": month,
            "selected_threshold": threshold,
            "page_sizes": [50, 100, 250, 500, 1000],
            "page_size": page_size,
            "people": people,
            'selected_person_id': person_id,
            'selected_assign_person_id': assign_person_id,
        }

        pagination = {
            "current_page": page,
            "total_items": unassigned_face_count,
            "page_size": page_size,
            "page_sizes": [50, 100, 250, 500, 1000],
        }

        return render_template(
            "faces/index.html", faces=unassigned_faces, people=people, face_suggestions=face_suggestions,
            filters=filters, unassigned_face_count=unassigned_face_count, pagination=pagination
        )

    @app.route("/api/faces/assign", methods=["POST"])
    def faces_assign():
        data = request.get_json()
        selected_face_ids = data.get("faces", [])
        person_id = data.get("person")
        face_status = data.get("faceStatus")
        try:
            if face_status == FACE_STATUS_IGNORED:
                db.session.query(Face).filter(Face.id.in_(selected_face_ids)).update(
                    {Face.status: face_status}, synchronize_session=False
                )
                db.session.commit()
                return jsonify({
                    "success": True,
                    "message": f"Successfully ignored {len(selected_face_ids)} face(s)",
                    "face_ids": selected_face_ids
                })

            elif selected_face_ids and person_id and face_status == FACE_STATUS_ASSIGNED:
                person: Person | None = (
                    Person.query.options(joinedload(Person.embeddings_by_year)).order_by(Person.name).get(
                        int(person_id)))
                if person is None:
                    error_msg = f'Person {person_id} not found'
                    logger.warn(error_msg)
                    return jsonify({"success": False, "message": error_msg}), 404

                faces = (Face.query.filter(Face.id.in_(selected_face_ids))).all()
                similarity_by_face_id = calculate_similarity(person, faces)

                db.session.query(PersonFace).filter(PersonFace.face_id.in_(selected_face_ids)).delete(
                    synchronize_session=False)

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
                update_person_embedding_task(person_id)
                return jsonify({
                    "success": True,
                    "message": f"Successfully assigned {len(selected_face_ids)} face(s) to {person.name}",
                    "face_ids": selected_face_ids
                })
            else:
                return jsonify({
                    "success": False,
                    "message": f"Invalid request faces, person, and face_status are required",
                }), 400
        except Exception as e:
            db.session.rollback()
            error_msg = f"Error processing faces: {str(e)}"
            logger.error(error_msg)
            return jsonify({"success": False, "message": error_msg}), 500
