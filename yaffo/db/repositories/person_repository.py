import json
from datetime import date

import numpy as np
from sqlalchemy import insert, text
from sqlalchemy.orm import Session
import pydash as _
from yaffo.db.models import Person, Face, PersonEmbedding, PersonFace
from yaffo.domain.compare_utils import (
    load_embedding,
    serialize_embedding,
    DEFAULT_SIMILARITY_FLOOR,
    DEFAULT_SIMILARITY_CEIL,
)
from yaffo.domain.life_stages import life_stage, effective_birthdate
from yaffo.logging_config import get_logger

logger = get_logger(__name__)

# Below this many assignments the percentiles are too noisy to calibrate a band.
_SIMILARITY_BOUNDS_MIN_SAMPLES = 50

# Nearest-rank low/high percentiles of stored similarities (SQLite has no
# percentile aggregate). Percentiles, not min/max, so a few mis-assignments at the
# tails don't stretch the band. COUNT(*) lets the caller fall back when sparse.
_SIMILARITY_BOUNDS_SQL = text("""
    SELECT
        (SELECT similarity FROM people_face WHERE similarity IS NOT NULL
         ORDER BY similarity
         LIMIT 1 OFFSET (SELECT CAST(:low * (COUNT(*) - 1) AS INT)
                         FROM people_face WHERE similarity IS NOT NULL)),
        (SELECT similarity FROM people_face WHERE similarity IS NOT NULL
         ORDER BY similarity
         LIMIT 1 OFFSET (SELECT CAST(:high * (COUNT(*) - 1) AS INT)
                         FROM people_face WHERE similarity IS NOT NULL)),
        (SELECT COUNT(*) FROM people_face WHERE similarity IS NOT NULL)
""")


def get_similarity_bounds(
    session: Session, low_pct: float = 0.05, high_pct: float = 0.95
) -> tuple[float, float]:
    """The (floor, ceil) cosine band the 0-100 UI similarity scale is calibrated
    against, read from the live distribution of assigned-face similarities. Falls
    back to the documented defaults until there are enough assignments to measure a
    stable band (or if the data is degenerate). Cheap enough to call once per
    request; the band drifts slowly as the gallery grows."""
    floor, ceil, n = session.execute(
        _SIMILARITY_BOUNDS_SQL, {"low": low_pct, "high": high_pct}
    ).one()
    if n < _SIMILARITY_BOUNDS_MIN_SAMPLES or floor is None or ceil is None or ceil <= floor:
        return DEFAULT_SIMILARITY_FLOOR, DEFAULT_SIMILARITY_CEIL
    return float(floor), float(ceil)


def _medoid(embeddings: list[np.ndarray]) -> np.ndarray:
    """The most representative real embedding of a group: the one closest (cosine)
    to the group centroid. A real face vector, not a blurry average -- so matching
    against it scores like a nearest-neighbour.

    Embeddings are unit-norm, so argmax over (emb · centroid) picks the same medoid
    whether or not the centroid is normalized -- and we deliberately do NOT
    normalize it: a diverse bucket's mean can be near-zero (e.g. near-orthogonal
    baby vs adult faces), and dividing by that tiny norm overflows float32."""
    # Drop any non-finite vectors (a degenerate face crop can yield NaN/inf), which
    # would otherwise poison the centroid and the argmax.
    finite = [e for e in embeddings if np.all(np.isfinite(e))]
    if len(finite) <= 1:
        return finite[0] if finite else embeddings[0]
    mat = np.vstack(finite)
    centroid = mat.mean(axis=0)
    # Dot to the centroid via an explicit reduction rather than `mat @ centroid`:
    # numpy's BLAS matmul (Apple Accelerate) emits spurious divide/overflow FPU
    # warnings here even though the inputs are unit vectors. Same result.
    sims = (mat * centroid).sum(axis=1)
    return finite[int(np.argmax(sims))]


MAX_REPRESENTATIVE_FACES = 200

# One representative face per capture-day -- the highest-confidence detection -- so
# a burst of near-identical frames from a single day can't drag the medoid toward
# that day. Faces whose photo has no parseable capture date can't be day-bucketed,
# so COALESCE gives each its own bucket (kept individually). The whole set is then
# capped to the MAX sharpest, bounding how many embedding blobs we deserialize no
# matter how prolific the person. SQLite's date() yields NULL for missing/garbage
# timestamps, and DESC sorts NULL det_scores last -- both matching the prior
# Python (-inf) behaviour.
_REP_FACES_SQL = text("""
    WITH daily AS (
        SELECT
            f.id            AS face_id,
            f.embedding     AS embedding,
            f.estimated_age AS estimated_age,
            p.year          AS year,
            f.det_score     AS det_score,
            ROW_NUMBER() OVER (
                PARTITION BY COALESCE(date(p.date_taken), 'u' || f.id)
                ORDER BY f.det_score DESC, f.id
            ) AS rn
        FROM people_face pf
        JOIN faces f       ON f.id = pf.face_id
        LEFT JOIN photos p ON p.id = f.photo_id
        WHERE pf.person_id = :person_id
    )
    SELECT face_id, embedding, estimated_age, year
    FROM daily
    WHERE rn = 1
    ORDER BY det_score DESC, face_id
    LIMIT :k
""")

# Median of (photo year - predicted age) over every face. Single-face age is noisy,
# but the median over many photos is robust. Computed DB-side so estimating the
# birthdate doesn't require loading any face rows. Returns NULL for an empty set;
# AVG of the middle one (odd) or two (even) values reproduces numpy's median.
_MEDIAN_BIRTH_YEAR_SQL = text("""
    WITH births AS (
        SELECT (p.year - f.estimated_age) AS b
        FROM people_face pf
        JOIN faces f  ON f.id = pf.face_id
        JOIN photos p ON p.id = f.photo_id
        WHERE pf.person_id = :person_id
          AND f.estimated_age IS NOT NULL
          AND p.year IS NOT NULL
    )
    SELECT AVG(b) FROM (
        SELECT b FROM births ORDER BY b
        LIMIT  2 - (SELECT COUNT(*) FROM births) % 2
        OFFSET (SELECT (COUNT(*) - 1) / 2 FROM births)
    )
""")


def get_person_by_id(session: Session, person_id: int) -> Person | None:
    return session.get(Person, person_id)


def get_photo_ids_for_person(session: Session, person_id: int) -> list[int]:
    """Distinct ids of photos that have at least one face linked to this person."""
    rows = (
        session.query(Face.photo_id)
        .join(PersonFace, PersonFace.face_id == Face.id)
        .filter(PersonFace.person_id == person_id, Face.photo_id.isnot(None))
        .distinct()
        .all()
    )
    return [photo_id for (photo_id,) in rows]


def get_people_with_embeddings(session: Session) -> list[Person]:
    """All people that have at least one per-stage embedding, so face-similarity
    calculations have something to compare against (and don't max() over empty)."""
    return [p for p in session.query(Person).all() if p.stage_embeddings]


def link_face_to_person(session: Session, person_id: int, face_id: int) -> bool:
    """Link one face to a person, unless it's already assigned (a face maps to one
    person). Returns whether a new link was made."""
    if session.query(PersonFace).filter_by(face_id=face_id).first() is not None:
        return False
    session.add(PersonFace(person_id=person_id, face_id=face_id))
    session.commit()
    return True


# Face ids per IN-clause — stay under SQLite's ~999 bound-param cap.
_LINK_CHUNK = 500


def bulk_link_faces_to_people(session: Session, links: list[tuple[int, int]]) -> int:
    """Link a batch of faces to people in one transaction, skipping any face already
    assigned (a face maps to one person). `links` is [(person_id, face_id), ...].
    Returns the number of new links created; commits once. Lets a batch job compute
    its matches lock-free and persist them in one short write."""
    if not links:
        return 0
    face_ids = [face_id for _, face_id in links]
    existing: set[int] = set()
    for start in range(0, len(face_ids), _LINK_CHUNK):
        chunk = face_ids[start:start + _LINK_CHUNK]
        existing.update(
            row[0] for row in session.query(PersonFace.face_id).filter(PersonFace.face_id.in_(chunk)).all()
        )
    rows = [
        {"person_id": person_id, "face_id": face_id}
        for person_id, face_id in links
        if face_id not in existing
    ]
    if rows:
        session.execute(insert(PersonFace), rows)
    session.commit()
    return len(rows)

def update_person_embedding(person_id: int, session):
    """Recompute a person's estimated birthdate and their per-life-stage medoid
    gallery from their assigned faces. Stage embeddings are derived data, rebuilt
    from scratch each time. Bucketing uses the effective birthdate (actual if set,
    else estimated); with no birthdate every face lands in the 'unknown' stage.

    Birthdate is a DB-side median over every face; the medoids use the daily-
    collapsed, top-N-by-confidence subset (_REP_FACES_SQL) so we deserialize at
    most MAX_REPRESENTATIVE_FACES embeddings instead of eager-loading every face."""
    try:
        person = session.get(Person, person_id)
        if person is None:
            return

        person.stage_embeddings.clear()  # delete-orphan removes old rows

        median_year = session.execute(
            _MEDIAN_BIRTH_YEAR_SQL, {"person_id": person_id}
        ).scalar()
        person.estimated_birthdate = (
            date(int(round(median_year)), 1, 1) if median_year is not None else None
        )

        rows = session.execute(
            _REP_FACES_SQL, {"person_id": person_id, "k": MAX_REPRESENTATIVE_FACES}
        ).all()
        if not rows:
            person.avg_embedding = None
            session.commit()
            return

        birthdate = effective_birthdate(person)  # actual wins, else the estimate above
        reps = [
            (r.face_id,
             load_embedding(r.embedding),
             life_stage(birthdate, r.year, r.estimated_age))
            for r in rows
        ]

        person.avg_embedding = serialize_embedding(_medoid([emb for _id, emb, _stage in reps]))

        session.flush()  # apply the clear() before inserting fresh rows (same PK)
        for stage, group in _.group_by(reps, lambda t: t[2]).items():
            person.stage_embeddings.append(PersonEmbedding(
                life_stage=stage,
                avg_embedding=serialize_embedding(_medoid([emb for _id, emb, _stage in group])),
                included_face_ids=json.dumps([face_id for face_id, _emb, _stage in group]),
            ))

        session.commit()
    except Exception as e:
        logger.error(f"Failed to update person embedding for {person_id}", e)