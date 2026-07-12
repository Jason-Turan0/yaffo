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
        LEFT JOIN media_items p ON p.id = f.media_item_id
        WHERE pf.person_id = :person_id
          AND f.embedding IS NOT NULL  -- nothing to deserialize into a medoid
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
        JOIN media_items p ON p.id = f.media_item_id
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


def existing_person_ids(session: Session, person_ids: list[int]) -> set[int]:
    """Which of `person_ids` are real people — one query, so a batch assign can drop
    unknown ids before linking."""
    if not person_ids:
        return set()
    unique = list(set(person_ids))
    found: set[int] = set()
    for start in range(0, len(unique), _LINK_CHUNK):
        chunk = unique[start:start + _LINK_CHUNK]
        found.update(row[0] for row in session.query(Person.id).filter(Person.id.in_(chunk)).all())
    return found


def get_media_item_ids_for_person(session: Session, person_id: int) -> list[int]:
    """Distinct ids of photos that have at least one face linked to this person."""
    rows = (
        session.query(Face.media_item_id)
        .join(PersonFace, PersonFace.face_id == Face.id)
        .filter(PersonFace.person_id == person_id, Face.media_item_id.isnot(None))
        .distinct()
        .all()
    )
    return [media_item_id for (media_item_id,) in rows]


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

# Faces scored (and written back) per pass. Keeps peak memory bounded on a person
# with tens of thousands of faces, and keeps each write transaction short -- the
# scoring itself is done lock-free, before any of it is flushed.
_SIMILARITY_CHUNK = 2000

# Every face assigned to a person, with what's needed to score it: the embedding, and
# the year + predicted age that place it in a life stage. A face with no embedding is
# still selected -- it can't be scored, so its score has to be cleared rather than left
# at whatever stale value it happens to hold.
_ASSIGNED_FACES_SQL = text("""
    SELECT
        pf.face_id      AS face_id,
        f.embedding     AS embedding,
        f.estimated_age AS estimated_age,
        m.year          AS year
    FROM people_face pf
    JOIN faces f ON f.id = pf.face_id
    LEFT JOIN media_items m ON m.id = f.media_item_id
    WHERE pf.person_id = :person_id
      AND (:only_missing = 0 OR pf.similarity IS NULL)
    ORDER BY pf.face_id
""")


def recompute_person_similarities(session: Session, person_id: int, only_missing: bool = False) -> int:
    """Rescore a person's assigned faces against their *current* stage gallery.

    PersonFace.similarity is a cache of "how much does this face look like this
    person", and the person it's measured against moves: every assignment rebuilds
    the medoid gallery (update_person_embedding), which silently invalidates every
    score written before it. This recomputes them, so a stored score always means the
    same thing as a freshly computed one.

    That matters beyond the number on the screen: the 0-100 scale the UI displays is
    calibrated against the *percentiles of these stored values* (get_similarity_bounds),
    so stale scores drag the band and mis-render even the faces that were scored
    correctly.

    Scoring matches domain.compare_utils.calculate_similarity, and must: the assignment
    screen and the auto-assign automation score with that one, and this cache is what
    the review screen sorts, filters and calibrates its scale from. Each face is scored
    against the medoid of *its own* life stage, falling back to the person's overall
    medoid for a stage they have no faces in. A person with no gallery at all gets NULL
    -- honestly "not known", rather than a number scored against nothing.

    `only_missing` restricts the pass to rows with no score, which is all that's
    needed when the gallery itself didn't move. Returns the number of rows written.
    """
    person = session.get(Person, person_id)
    if person is None:
        return 0

    stage_medoids = {
        stage.life_stage: load_embedding(stage.avg_embedding)
        for stage in person.stage_embeddings
        if stage.avg_embedding
    }
    overall_medoid = load_embedding(person.avg_embedding) if person.avg_embedding else None
    birthdate = effective_birthdate(person)

    rows = session.execute(
        _ASSIGNED_FACES_SQL,
        {"person_id": person_id, "only_missing": 1 if only_missing else 0},
    ).all()
    if not rows:
        return 0

    # A face is scored against the medoid of its own life stage; a stage the person has
    # no faces in falls back to their overall medoid. Group the faces by the medoid they
    # land on, so each group is one matmul rather than a dot product per face. Mirrors
    # compare_utils.reference_embedding_for_face -- same rule, off SQL columns rather
    # than ORM objects.
    _OVERALL = "__overall__"
    updates: list[dict] = []
    rows_by_reference: dict[str, list] = {}
    for row in rows:
        stage = life_stage(birthdate, row.year, row.estimated_age) if row.embedding else None
        reference_key = stage if stage in stage_medoids else _OVERALL
        if not row.embedding or (reference_key == _OVERALL and overall_medoid is None):
            # Nothing to compare against: no embedding on the face, or no gallery at all.
            updates.append({"face_id": row.face_id, "similarity": None})
            continue
        rows_by_reference.setdefault(reference_key, []).append(row)

    for reference_key, scored_rows in rows_by_reference.items():
        reference = overall_medoid if reference_key == _OVERALL else stage_medoids[reference_key]
        for start in range(0, len(scored_rows), _SIMILARITY_CHUNK):
            chunk = scored_rows[start:start + _SIMILARITY_CHUNK]
            embeddings = np.stack([load_embedding(row.embedding) for row in chunk])
            # Embeddings are L2-normalized, so the dot product IS the cosine.
            #
            # errstate: numpy's Accelerate BLAS backend (macOS) raises spurious
            # divide-by-zero/overflow flags on float32 matmul -- sklearn's own
            # cosine_similarity trips them on this same data, and the results agree to
            # 3e-07 and stay finite. Nothing here can legitimately divide or overflow.
            with np.errstate(all="ignore"):
                scores = embeddings @ reference
            updates.extend(
                {"face_id": row.face_id, "similarity": float(score)}
                for row, score in zip(chunk, scores)
            )

    # Compute first, write second: the scoring above holds no write lock, and the
    # flush below takes one only in short chunks (see the SQLite concurrency notes
    # in yaffo/db/__init__.py).
    for start in range(0, len(updates), _SIMILARITY_CHUNK):
        session.execute(
            text("UPDATE people_face SET similarity = :similarity WHERE face_id = :face_id"),
            updates[start:start + _SIMILARITY_CHUNK],
        )
        session.commit()
    return len(updates)

def update_person_embedding(person_id: int, session):
    """Recompute a person's estimated birthdate and their per-life-stage medoid
    gallery from their assigned faces. Stage embeddings are derived data, rebuilt
    from scratch each time. Bucketing uses the effective birthdate (actual if set,
    else estimated); with no birthdate every face lands in the 'unknown' stage.

    Birthdate is a DB-side median over every face; the medoids use the daily-
    collapsed, top-N-by-confidence subset (_REP_FACES_SQL) so we deserialize at
    most MAX_REPRESENTATIVE_FACES embeddings instead of eager-loading every face.

    Rebuilding the gallery invalidates the cached PersonFace.similarity of every face
    scored against the old one, so this rescores them afterwards. When the gallery
    comes back unchanged -- the common case, since the medoids are drawn from at most
    MAX_REPRESENTATIVE_FACES and a few new faces rarely move them -- only the faces
    still missing a score are touched, not all of them."""
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
            # No faces left to build a gallery from, so nothing can be scored against
            # it: any surviving scores are meaningless and go back to NULL.
            recompute_person_similarities(session, person_id)
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

        written = recompute_person_similarities(session, person_id, only_missing=False)
        logger.debug(
            f"rescored {written} face(s) for person {person_id} "
        )
    except Exception as e:
        logger.error(f"Failed to update person embedding for {person_id}", e)