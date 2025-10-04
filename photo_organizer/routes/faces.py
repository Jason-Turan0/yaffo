from dataclasses import dataclass
from typing import Optional, Tuple, List
import numpy as np
from flask import Flask, render_template, request, redirect, url_for
from joblib.externals.loky.backend.reduction import DEFAULT_ENV
from sqlalchemy import extract
from sqlalchemy.dialects.sqlite import insert
import pydash as _
from sqlalchemy.orm import joinedload

from photo_organizer.db.models import db, Face, Person, PersonFace, FACE_STATUS_UNASSIGNED, FACE_STATUS_IGNORED, \
    FACE_STATUS_ASSIGNED, Photo, PersonEmbedding
from sklearn.metrics.pairwise import cosine_similarity

from photo_organizer.db.repositories.person_repository import update_person_embedding
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




def init_faces_route(app: Flask):
    @app.route("/faces", methods=["GET"])
    def faces_index():
        query = (
                db.session.query(Face)
                    .join(Face.photo)
                    .outerjoin(Face.people)
                 )
        year = request.args.get("year", type=int)
        month = request.args.get("month", type=int)
        status = request.args.get("status", type=str)
        threshold = request.args.get("threshold", type=float)
        filter_threshold = threshold if threshold else DEFAULT_THRESHOLD
        if year:
            # Assuming Face has a relationship to Photo, and Photo.date_taken is a datetime
            query = query.filter(extract("year", Photo.date_taken) == year)
        if month:
            query = query.filter(extract("month", Photo.date_taken) == month)
        if status:
            query = query.filter(Face.status == status.upper())
        else:
            query = query.filter(Face.status == FACE_STATUS_UNASSIGNED)
        unassigned_faces :List[Face] = query.limit(FACE_LOAD_LIMIT).all()
        filters = {
            "years": [2025],
            "months": [1,2,3,4,5,6,7,8,9,10,11,12],
            "statuses": [FACE_STATUS_UNASSIGNED, FACE_STATUS_IGNORED],
            "threshold": filter_threshold,
        }


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

            def flat_map_people(person: Person) -> List[Tuple[Person, np.ndarray]]:
                return [(person, load_embedding(embedding_by_year.avg_embedding))
                        for embedding_by_year in person.embeddings_by_year]

            matching_people : List[Tuple[Person, float]] = (
                _.chain(people)
                 .flat_map(flat_map_people)
                 .map(lambda pair: (pair[0], cosine_similarity([emb], [pair[1]])[0][0]))
                 .filter(lambda pair: pair[1] > filter_threshold)
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

        return render_template(
            "faces/index.html", faces=unassigned_faces, people=people, face_suggestions=face_suggestions, filters=filters
        )

    @app.route("/faces/assign", methods=["POST"])
    def faces_assign():
        # Get selected face IDs from form
        selected_face_ids = request.form.getlist("faces")
        person_id = request.form.get("person")
        face_status = request.form.get("face_status")

        if face_status == FACE_STATUS_IGNORED:
            db.session.query(Face).filter(Face.id.in_(selected_face_ids)).update(
                {Face.status: face_status}, synchronize_session=False
            )
            db.session.commit()

        elif selected_face_ids and person_id and face_status == FACE_STATUS_ASSIGNED:
            person : Person | None = (Person.query.options(joinedload(Person.embeddings_by_year)).order_by(Person.name).get(int(person_id)))
            if person is None:
                print(f'Person {person_id} not found')
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
        return redirect(request.referrer or url_for("faces_index"))

    @app.route("/faces/remove", methods=["POST"])
    def faces_remove():
        # Get selected face IDs from form (as list of strings)
        selected_face_ids = request.form.getlist("faces")
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
        return redirect(request.referrer or url_for("faces_index"))