import json
from dataclasses import dataclass
from typing import NamedTuple, Optional, List
import numpy as np
from flask import Flask, jsonify, render_template, request
from flask_babel import gettext
from sqlalchemy import func

from yaffo.logging_config import get_logger

import pydash as _
from sqlalchemy.orm import joinedload
from yaffo.db.models import (
    db, Face, Person, PersonFace, FACE_STATUS_IGNORED, FACE_STATUS_UNASSIGNED, MediaItem, ApplicationSettings,
)

from sklearn.metrics.pairwise import cosine_similarity

from yaffo.db.repositories.person_repository import get_similarity_bounds
from yaffo.db.repositories.media_repository import get_distinct_years, get_distinct_months
from yaffo.domain.compare_utils import load_embedding, similarity_to_ui_fraction, ui_threshold_to_similarity
from yaffo.domain.face_clusters import cluster_faces
from yaffo.utils.context import context
from yaffo.utils.photo_dates import parse_date_taken

DEFAULT_THRESHOLD = 50  # UI similarity slider 0-100 (0 = least similar, 100 = most)
DEFAULT_BATCH_SIZE = 2000  # max unassigned faces pulled + clustered per pass
DEFAULT_GROUP_BY = 'similarity'
# Faces rendered as thumbnails per cluster. The whole cluster is still assigned;
# this only caps how many we paint so a 50k batch stays responsive.
SAMPLE_SIZE = 50
SHORTCUT_LIMIT = 9
FACE_SHORTCUT_PEOPLE_SETTING = "face_shortcut_people"


@dataclass
class FaceViewModel:
    id: int
    photo_date: str
    # The best similarity on the threshold slider's scale, as a 0.0-1.0 fraction (the
    # client shows it as a percent). Filled in by the route, which knows the band.
    similarity: Optional[float]

    # The same score as a raw cosine similarity.
    cosine_similarity: Optional[float]
    # The source media the face was cropped from — the hover preview shows it.
    media_item_id: int
    media_type: str
    # Detection box in source-image pixels, so the preview can outline the face.
    # Absent on faces indexed before the box was recorded.
    region: Optional[dict]
    # Shown only when the "Include ignored faces" filter is on; the grid marks them.
    ignored: bool = False


@dataclass
class FaceSuggestion:
    person_ids: list[int]
    people: list[Person]
    suggestion_name: str
    photo_date: str
    faces: list[FaceViewModel]
    date_start: Optional[str] = None
    date_end: Optional[str] = None


class StageMatch(NamedTuple):
    """How closely a face matches one of a person's life-stage embeddings."""
    person: Person
    life_stage: str
    similarity: float


logger = get_logger(__name__, 'webapp')


def _person_shortcut(person: Person) -> dict:
    return {"id": person.id, "name": person.name}


def _default_shortcut_people() -> list[dict]:
    return [
        _person_shortcut(person)
        for person in (
            db.session.query(Person)
            .outerjoin(PersonFace)
            .group_by(Person.id)
            .order_by(func.count(PersonFace.face_id).desc(), Person.name)
            .limit(SHORTCUT_LIMIT)
            .all()
        )
    ]


def _saved_shortcut_person_ids() -> list[int] | None:
    setting = db.session.query(ApplicationSettings).filter_by(name=FACE_SHORTCUT_PEOPLE_SETTING).first()
    if setting is None:
        return None
    try:
        raw = json.loads(setting.value or "[]")
    except (TypeError, ValueError):
        return None
    if not isinstance(raw, list):
        return None
    person_ids: list[int] = []
    for item in raw:
        try:
            person_id = int(item)
        except (TypeError, ValueError):
            continue
        if person_id not in person_ids:
            person_ids.append(person_id)
    return person_ids[:SHORTCUT_LIMIT]


def _people_for_shortcut_ids(people: list[Person], person_ids: list[int]) -> list[dict]:
    by_id = {person.id: person for person in people}
    return [
        _person_shortcut(by_id[person_id])
        for person_id in person_ids
        if person_id in by_id
    ]


def _save_shortcut_person_ids(person_ids: list[int]) -> None:
    setting = db.session.query(ApplicationSettings).filter_by(name=FACE_SHORTCUT_PEOPLE_SETTING).first()
    value = json.dumps(person_ids[:SHORTCUT_LIMIT])
    if setting is None:
        db.session.add(ApplicationSettings(name=FACE_SHORTCUT_PEOPLE_SETTING, type="json", value=value))
    else:
        setting.value = value
    db.session.commit()


def _reset_shortcut_people() -> None:
    setting = db.session.query(ApplicationSettings).filter_by(name=FACE_SHORTCUT_PEOPLE_SETTING).first()
    if setting is not None:
        db.session.delete(setting)
        db.session.commit()


def _face_region(face: Face) -> Optional[dict]:
    box = (face.location_top, face.location_right, face.location_bottom, face.location_left)
    if any(coordinate is None for coordinate in box):
        return None
    top, right, bottom, left = box
    return {"top": top, "right": right, "bottom": bottom, "left": left}


def _face_view_model(face: Face, cosign_similarity: Optional[float]) -> FaceViewModel:
    return FaceViewModel(
        id=face.id,
        photo_date=face.media_item.date_taken,
        similarity=None,  # slider-scale; the route converts cosine_similarity once it has the band
        cosine_similarity=cosign_similarity,
        media_item_id=face.media_item.id,
        media_type=face.media_item.media_type,
        region=_face_region(face),
        ignored=face.status == FACE_STATUS_IGNORED,
    )


def _centroid_similarities(embeddings: list[np.ndarray]) -> list[float]:
    """Cosine similarity of each embedding to the mean of the group."""
    matrix = np.array(embeddings, dtype=np.float64)
    centroid = matrix.mean(axis=0)
    centroid_norm = np.linalg.norm(centroid)
    if centroid_norm == 0:  # antipodal members cancel out; no meaningful centre
        return [0.0] * len(embeddings)
    centroid = centroid / centroid_norm
    norms = np.linalg.norm(matrix, axis=1)
    norms[norms == 0] = 1.0
    similarities = (matrix @ centroid) / norms
    return [float(value) for value in np.clip(similarities, 0.0, 1.0)]


def make_suggestions_by_similarity(unassigned_faces: list[Face], min_similarity: float) -> list[FaceSuggestion]:
    embeddings = []
    face_ids = []
    face_dict = {face.id: face for face in unassigned_faces}
    if len(unassigned_faces) == 0:
        return []

    for face in unassigned_faces:
        embeddings.append(load_embedding(face.embedding))
        face_ids.append(face.id)
    embedding_by_face_id = dict(zip(face_ids, embeddings))
    clusters = {
        label: {"label": gettext("Cluster %(number)s", number=label), "face_ids": ids}
        for label, ids in cluster_faces(face_ids, embeddings, min_similarity)
    }

    suggestions = []
    for cluster in clusters.values():
        # A similarity cluster has no person to score against, so each face is
        # scored against the cluster's own centroid: how representative it is of
        # the group. Weak members sort to the bottom and are easy to deselect.
        similarities = _centroid_similarities(
            [embedding_by_face_id[face_id] for face_id in cluster["face_ids"]]
        )
        suggestions.append(FaceSuggestion(
            person_ids=[],
            people=[],
            suggestion_name=cluster["label"],
            photo_date=face_dict[cluster["face_ids"][0]].media_item.date_taken,
            faces=[
                _face_view_model(face_dict[face_id], similarity)
                for face_id, similarity in zip(cluster["face_ids"], similarities)
            ],
        ))
    suggestions.sort(key=lambda suggestion: len(suggestion.faces), reverse=True)
    return suggestions


def make_suggestions_for_people(unassigned_faces: list[Face], people: list[Person], min_cosine_similarity: float,
                                person_ids: list[int]) -> list[
    FaceSuggestion]:
    face_suggestions = []
    default_suggestion = FaceSuggestion(
        person_ids=[],
        people=[],
        suggestion_name=gettext("Unknown"),
        photo_date='',
        faces=[]
    )

    # min_cosine_similarity is the required cosine similarity, already scaled from the UI slider
    for face in unassigned_faces:
        emb = load_embedding(face.embedding)

        def stage_matches(person: Person) -> List[StageMatch]:
            return [StageMatch(person, stage_emb.life_stage,
                               float(cosine_similarity([emb], [load_embedding(stage_emb.avg_embedding)])[0][0]))
                    for stage_emb in person.stage_embeddings]

        # Each matching person's best life stage, best match first: grouping keeps
        # the first (highest) stage per person, in the sorted order.
        matching_people: List[StageMatch] = (
            _.chain(people)
            .flat_map(stage_matches)
            .filter(lambda match: match.similarity > min_cosine_similarity
                    and (not person_ids or match.person.id in person_ids))
            .sort_by(lambda match: match.similarity, True)
            .group_by(lambda match: match.person.id)
            .values()
            .map(lambda matches_for_person: matches_for_person[0])
            .value()
        )
        best_suggestion: FaceSuggestion | None = next(
            (suggestion for suggestion in face_suggestions
             if set(suggestion.person_ids) == {match.person.id for match in matching_people}), None
        )
        if best_suggestion is None and len(matching_people) > 0:
            best_suggestion = FaceSuggestion(
                person_ids=[match.person.id for match in matching_people],
                people=[match.person for match in matching_people],
                suggestion_name=gettext(" OR ").join([match.person.name for match in matching_people]),
                photo_date=face.media_item.date_taken,
                faces=[]
            )
            face_suggestions.append(best_suggestion)

        if best_suggestion is not None:
            best_suggestion.faces.append(_face_view_model(face, matching_people[0].similarity))
        else:
            default_suggestion.faces.append(_face_view_model(face, None))

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
            .join(Face.media_item)
            .options(joinedload(Face.media_item))
            .outerjoin(Face.people)
        )
        year = request.args.get("year", type=int)
        month = request.args.get("month", type=int)
        threshold = request.args.get("threshold", default=DEFAULT_THRESHOLD, type=int)
        batch_size = request.args.get("batch_size", default=DEFAULT_BATCH_SIZE, type=int)
        person_ids = [int(value) for value in request.args.getlist("person") if value.isdigit()]
        assign_person_id = request.args.get("assign_person", type=int)
        group_by = request.args.get("group_by", type=str, default=DEFAULT_GROUP_BY)
        include_ignored = request.args.get("include_ignored") == "1"

        if year:
            query = query.filter(MediaItem.year == year)
        if month:
            query = query.filter(MediaItem.month == month)
        # Ignored faces join the pile only on request, so a past "ignore" can be revisited.
        statuses = [FACE_STATUS_UNASSIGNED, FACE_STATUS_IGNORED] if include_ignored else [FACE_STATUS_UNASSIGNED]
        query = query.filter(Face.status.in_(statuses)).order_by(MediaItem.date_taken)

        unassigned_face_count = query.count()
        # Each pass pulls and clusters up to batch_size unassigned faces. The UI
        # works through the resulting clusters then reloads for the next pass.
        unassigned_faces: List[Face] = query.limit(batch_size).all()

        people = (db.session.query(Person)
                  .outerjoin(PersonFace)
                  .group_by(Person.id)
                  .options(joinedload(Person.stage_embeddings))
                  .order_by(Person.name)
                  .all()
                  )

        # Similarity clusters aren't tied to specific people, so the number-key
        # shortcuts fall back to the most frequently assigned people until the
        # user saves an explicit shortcut list.
        default_shortcut_people = _default_shortcut_people()
        saved_shortcut_person_ids = _saved_shortcut_person_ids()
        shortcut_people_customized = saved_shortcut_person_ids is not None
        shortcut_people = (
            _people_for_shortcut_ids(people, saved_shortcut_person_ids or [])
            if shortcut_people_customized else default_shortcut_people
        )
        all_people_shortcuts = [_person_shortcut(person) for person in people]

        # Scale the 0-100 slider to a cosine similarity against the live data band.
        bounds = get_similarity_bounds(db.session)
        min_cosign_similarity = ui_threshold_to_similarity(threshold, *bounds)
        face_suggestions = (
            make_suggestions_by_similarity(unassigned_faces, min_cosign_similarity)) \
            if (group_by == 'similarity') else \
            make_suggestions_for_people(unassigned_faces, people, min_cosign_similarity, person_ids)

        for suggestion in face_suggestions:
            # People mode: show the face-vs-person score on the slider's scale, so a
            # face reads against the threshold that let it in. Similarity mode scores
            # fit to the cluster's own centroid -- a different, much higher measure
            # the band would clamp to 100% -- so it stays as is.
            for face_view in suggestion.faces:
                if face_view.cosine_similarity is not None:
                    face_view.similarity = (face_view.cosine_similarity if group_by == 'similarity'
                                            else similarity_to_ui_fraction(face_view.cosine_similarity, *bounds))
            # Ascending: the weakest matches lead, so the faces most likely to be
            # wrong are the ones you see first and can deselect before assigning.
            # Sorted on the raw cosine, which the slider scale clamps into ties.
            suggestion.faces = _.sort_by(
                suggestion.faces, lambda f: f.cosine_similarity if f.cosine_similarity is not None else 0)
            # Show the cluster's capture-date span. Pick min/max by parsed datetime
            # (robust to mixed separators) but keep the original strings to format.
            dated = [(f.photo_date, parse_date_taken(f.photo_date)) for f in suggestion.faces]
            dated = [(raw, dt) for raw, dt in dated if dt is not None]
            if dated:
                suggestion.date_start = min(dated, key=lambda x: x[1])[0]
                suggestion.date_end = max(dated, key=lambda x: x[1])[0]
        months = get_distinct_months()
        years = get_distinct_years(db.session)
        filters = {
            "years": years,
            "selected_year": year,
            "months": months,
            "selected_month": month,
            "selected_threshold": threshold,
            "people": people,
            'selected_person_ids': person_ids,
            'selected_assign_person_id': assign_person_id,
            "selected_group_by": group_by,
            "selected_batch_size": batch_size,
            "selected_include_ignored": include_ignored,
            "batch_sizes": [25, 50, 100, 250, 500, 1000, 2000, 5000, 10000, 20000, 50000],
        }

        return render_template(
            "faces/index.html", faces=unassigned_faces, people=people, face_suggestions=face_suggestions,
            filters=filters, unassigned_face_count=unassigned_face_count,
            sample_size=SAMPLE_SIZE, shortcut_people=shortcut_people,
            default_shortcut_people=default_shortcut_people,
            all_people_shortcuts=all_people_shortcuts,
            shortcut_people_customized=shortcut_people_customized,
        )

    @app.route("/settings/faces/shortcuts", methods=["POST", "DELETE"])
    def face_shortcut_people_settings():
        if request.method == "DELETE":
            _reset_shortcut_people()
            return "", 204

        payload = request.get_json(silent=True) or {}
        raw_person_ids = payload.get("person_ids")
        if not isinstance(raw_person_ids, list):
            return {
                "error": gettext("Items must be a list"),
                "code": "items_must_be_list",
            }, 400

        person_ids: list[int] = []
        for raw_id in raw_person_ids:
            try:
                person_id = int(raw_id)
            except (TypeError, ValueError):
                continue
            if person_id not in person_ids:
                person_ids.append(person_id)
            if len(person_ids) == SHORTCUT_LIMIT:
                break

        if person_ids:
            known_ids = {
                person_id
                for person_id, in db.session.query(Person.id).filter(Person.id.in_(person_ids)).all()
            }
            if known_ids != set(person_ids):
                return {
                    "error": gettext("Person not found"),
                    "code": "person_not_found",
                }, 404

        _save_shortcut_person_ids(person_ids)
        return "", 204

    @app.route("/faces/people/create", methods=["POST"])
    def faces_person_create():
        """The Faces page's "Create Person": JSON in and out, for its sidebar. The
        People page has its own form version (people_create)."""
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
