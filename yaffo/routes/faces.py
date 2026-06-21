import threading
from dataclasses import dataclass
from typing import Optional, Tuple, List
import numpy as np
from flask import Flask, render_template, request, jsonify
from sklearn.cluster import DBSCAN

from yaffo.background_tasks.tasks.assign_faces_to_person import assign_faces_to_person
from yaffo.logging_config import get_logger

import pydash as _
from sqlalchemy.orm import joinedload
from yaffo.db.models import db, Face, Person, PersonFace, FACE_STATUS_UNASSIGNED, FACE_STATUS_IGNORED, \
    FACE_STATUS_ASSIGNED, Photo, PHOTO_STATUS_INDEXED, EVENT_PHOTO_MODIFIED, FACE_STATUS_PROCESSING

from sklearn.metrics.pairwise import cosine_similarity

from yaffo.db.repositories.person_repository import update_person_embedding, get_similarity_bounds
from yaffo.db.repositories.photos_repository import get_distinct_years, get_distinct_months
from yaffo.domain.compare_utils import load_embedding, calculate_similarity, ui_threshold_to_similarity
from yaffo.utils.context import context

DEFAULT_THRESHOLD = 85  # UI similarity slider 0-100 (0 = least similar, 100 = most)
DEFAULT_PAGE_SIZE = 50000
DEFAULT_MIN_SAMPLE_SIZE = 3
DEFAULT_GROUP_BY = 'similarity'


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
    photo_date: str
    faces: list[FaceViewModel]


logger = get_logger(__name__, 'webapp')


def make_suggestions_by_similarity(unassigned_faces: list[Face], min_similarity: float) -> list[FaceSuggestion]:
    embeddings = []
    face_ids = []
    face_dict = {face.id: face for face in unassigned_faces}
    if len(unassigned_faces) == 0:
        return []

    for face in unassigned_faces:
        embeddings.append(load_embedding(face.embedding))
        face_ids.append(face.id)
    embeddings = np.array(embeddings)
    # ArcFace embeddings are L2-normalized -> cluster by cosine distance (1 - cos).
    # min_similarity is the required cosine similarity (already scaled from the UI
    # slider); eps is the complementary distance radius, so requiring more
    # similarity tightens the clusters.
    eps = 1.0 - min_similarity
    clustering = DBSCAN(eps=eps, min_samples=DEFAULT_MIN_SAMPLE_SIZE, metric="cosine").fit(embeddings)
    clusters = {}
    for face_id, label in zip(face_ids, clustering.labels_):
        if label == -1:  # skip noise faces
            continue
        label = f"Cluster {label}"
        cluster = clusters[label] if label in clusters else {'label': label, 'face_ids': []}
        clusters[label] = cluster
        cluster["face_ids"].append(face_id)

    suggestions = [FaceSuggestion(
        person_ids=[],
        people=[],
        suggestion_name=cluster["label"],
        photo_date=face_dict[cluster["face_ids"][0]].photo.date_taken,
        faces=[
            FaceViewModel(face_id, face_dict[face_id].full_file_path, face_dict[face_id].photo.date_taken, None)
            for face_id in cluster["face_ids"]
        ],
    ) for cluster in clusters.values()]
    suggestions.sort(key=lambda suggestion: len(suggestion.faces), reverse=True)
    return suggestions


def make_suggestions_for_people(unassigned_faces: list[Face], people: list[Person], min_similarity: float, person_id) -> list[
    FaceSuggestion]:
    face_suggestions = []
    default_suggestion = FaceSuggestion(
        person_ids=[],
        people=[],
        suggestion_name='Unknown',
        photo_date='',
        faces=[]
    )
    # min_similarity is the required cosine similarity, already scaled from the UI
    # slider against the live similarity band.
    computed_threshold = min_similarity
    for face in unassigned_faces:
        emb = load_embedding(face.embedding)

        def flat_map_people(person: Person) -> List[Tuple[Person, str, np.ndarray]]:
            return [(person, stage_emb.life_stage, load_embedding(stage_emb.avg_embedding))
                    for stage_emb in person.stage_embeddings]

        matching_people: List[Tuple[Person, float]] = (
            _.chain(people)
            .flat_map(flat_map_people)
            .map(lambda tuple: (tuple[0], tuple[1], cosine_similarity([emb], [tuple[2]])[0][0]))
            .filter(lambda tuple: tuple[2] > computed_threshold and (person_id is None or tuple[0].id == person_id))
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
                photo_date=face.photo.date_taken,
                faces=[]
            )
            face_suggestions.append(best_suggestion)

        if best_suggestion is not None:
            best_sim = matching_people[0][2]
            best_suggestion.faces.append(
                FaceViewModel(face.id, face.full_file_path, face.photo.date_taken, best_sim))
        else:
            default_suggestion.faces.append(
                FaceViewModel(face.id, face.full_file_path, face.photo.date_taken, None))

    face_suggestions.sort(key=lambda suggestion: (1 if len(suggestion.person_ids) == 1 else 0, len(suggestion.faces)),
                          reverse=True)
    if len(default_suggestion.faces) > 0:
        face_suggestions.append(default_suggestion)
    return face_suggestions


@context("yaffo-face_assignment")
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
        threshold = request.args.get("threshold", default=DEFAULT_THRESHOLD, type=int)
        page = request.args.get("page", default=1, type=int)
        page_size = request.args.get("page-size", default=DEFAULT_PAGE_SIZE, type=int)
        person_id = request.args.get("person", type=int)
        assign_person_id = request.args.get("assign_person", type=int)
        group_by = request.args.get("group_by", type=str, default=DEFAULT_GROUP_BY)

        if year:
            query = query.filter(Photo.year == year)
        if month:
            query = query.filter(Photo.month == month)
        query = query.filter(Face.status == FACE_STATUS_UNASSIGNED).order_by(Photo.date_taken)

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
                  .options(joinedload(Person.stage_embeddings))
                  .order_by(Person.name)
                  .all()
                  )

        # Scale the 0-100 slider to a cosine similarity against the live data band.
        min_similarity = ui_threshold_to_similarity(threshold, *get_similarity_bounds(db.session))
        face_suggestions = (
            make_suggestions_by_similarity(unassigned_faces, min_similarity)) \
            if (group_by == 'similarity') else \
            make_suggestions_for_people(unassigned_faces, people, min_similarity, person_id)

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
            "people": people,
            'selected_person_id': person_id,
            'selected_assign_person_id': assign_person_id,
            "selected_group_by": group_by,
        }

        pagination = {
            "current_page": page,
            "total_items": unassigned_face_count,
            "page_size": page_size,
            "page_sizes": [25, 50, 100, 250, 500, 1000, 2000, 5000, 10000, 20000, 50000],
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
                    Person.query.options(joinedload(Person.stage_embeddings)).order_by(Person.name).get(
                        int(person_id)))
                if person is None:
                    error_msg = f'Person {person_id} not found'
                    logger.warn(error_msg)
                    return jsonify({"success": False, "message": error_msg}), 404

                db.session.query(Face).filter(Face.id.in_(selected_face_ids)).update(
                    {Face.status: FACE_STATUS_PROCESSING}, synchronize_session=False
                )
                db.session.commit()
                assign_faces_to_person(person_id, selected_face_ids)
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
