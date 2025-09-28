from dataclasses import dataclass
from typing import Optional

import numpy as np
from flask import Flask, render_template, request, redirect, url_for
from sqlalchemy import extract
from sqlalchemy.dialects.sqlite import insert

from photo_organizer.db.models import db, Face, Person, PersonFace, FACE_STATUS_UNASSIGNED, FACE_STATUS_IGNORED, \
    FACE_STATUS_ASSIGNED, Photo
from sklearn.metrics.pairwise import cosine_similarity

THRESHOLD = 0.95  # configurable similarity threshold
FACE_LOAD_LIMIT = 500

def load_embedding(blob: bytes) -> np.ndarray:
    arr = np.frombuffer(blob, dtype=np.float64)
    return arr.reshape((128,))

@dataclass
class FaceSuggestion:
    person_id: Optional[int]
    person: Optional[Person]
    suggestion_name: str
    faces: list[Face]

def update_person_embedding(person_id:  str | None):
    person = Person.query.get(person_id)
    if person is None:
        return
    embeddings = [load_embedding(face.embedding) for face in person.faces]
    if len(embeddings) > 0:
        avg = np.mean(embeddings, axis=0)
        person.avg_embedding = avg.tobytes()
        db.session.commit()

def init_faces_route(app: Flask):
    @app.route("/faces", methods=["GET"])
    def faces_index():
        query = db.session.query(Face).filter(Face.status == FACE_STATUS_UNASSIGNED)
        year = request.args.get("year", type=int)
        month = request.args.get("month", type=int)
        if year:
            # Assuming Face has a relationship to Photo, and Photo.date_taken is a datetime
            query = query.join(Face.photo).filter(extract("year", Photo.date_taken) == year)
        if month:
            query = query.filter(extract("month", Photo.date_taken) == month)

        unassigned_faces = query.limit(FACE_LOAD_LIMIT).all()


        people = db.session.query(Person).order_by(Person.name).all()
        # Compute suggested person for each face
        face_suggestions : list[FaceSuggestion] = [
            FaceSuggestion(
                person_id=person.id,
                person=person,
                suggestion_name= person.name,
                faces=[]
            ) for person in people
        ]
        default_suggestion = FaceSuggestion(
            person_id=None,
            person=None,
            suggestion_name='Unknown',
            faces=[]
        )
        face_suggestions.append(default_suggestion)
        for face in unassigned_faces:
            best_sim = None
            emb = load_embedding(face.embedding)
            best_suggestion = None
            for suggestion in face_suggestions:
                if suggestion.person is not None and suggestion.person.avg_embedding is not None:
                    sim = cosine_similarity([emb], [load_embedding(suggestion.person.avg_embedding)])[0][0]
                    if best_sim is None or best_sim < sim:
                        best_sim = sim
                        best_suggestion = suggestion
            if best_sim is None or best_sim < THRESHOLD or best_suggestion is None:
                default_suggestion.faces.append(face)
            else:
                best_suggestion.faces.append(face)

        return render_template(
            "faces/index.html", faces=unassigned_faces, people=people, face_suggestions=face_suggestions
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

            stmt = insert(PersonFace).values([
                {"person_id": person_id, "face_id": fid}
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
            update_person_embedding(person_id)


        return redirect(url_for("faces_index"))
