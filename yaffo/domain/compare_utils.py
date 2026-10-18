import numpy as np
from yaffo.db.models import Face, Person
from sklearn.metrics.pairwise import cosine_similarity

from yaffo.domain.life_stages import effective_birthdate, life_stage
from yaffo.utils.face_analysis import MODEL_NAME as FACE_MODEL_NAME

def load_embedding(blob: bytes) -> np.ndarray:
    """Deserialize a stored face embedding. ArcFace embeddings are 512-d float32
    (see yaffo/utils/face_analysis); dimensionality is left implicit so the same
    code works regardless of model."""
    return np.frombuffer(blob, dtype=np.float32).reshape(-1)


def serialize_embedding(arr: np.ndarray) -> bytes:
    """Serialize an embedding for storage. Always float32 so reads via
    load_embedding are consistent (np.mean etc. otherwise widen to float64)."""
    return np.asarray(arr, dtype=np.float32).tobytes()


# The 0-100 UI similarity scale, as a cosine band per face model. Raw cosine doesn't
# usefully span [0, 1] -- unrelated faces sit near 0 and strong matches top out
# around 0.8 -- so every slider, displayed percent and automation threshold maps onto
# [floor, ceil] instead. 0% = no more alike than two unrelated relatives; 100% = a
# strong match. The band is a property of the model, not stored in it: it's measured
# (benchmarks/face/ run.py prints a calibration table) and changes between models, so
# a new MODEL_NAME needs its own entry -- and a reseed anyway, since embeddings from
# different models aren't comparable.
#
# Fixed, not derived from the library: a band measured from people_face scores fed
# back into itself (wrong assignments lowered the floor, which loosened every
# threshold, which let more in) and silently changed what saved thresholds meant.
# buffalo_l (2026-10-18): the floor is low on purpose, so a person's weakest faces
# still read as a gradient rather than a wall of 0% (at 0.35, 12% of a child's faces
# clamped); 0.10 is about where unrelated family members score in the benchmark
# (median 0.11). Strong matches peak at 0.75-0.82. Family look-alikes (siblings) sit
# around 0.45-0.55, i.e. roughly 50-65% on this scale -- so automation thresholds
# belong above that (auto-assign at 55 = 0.485).
SIMILARITY_BANDS: dict[str, tuple[float, float]] = {
    "buffalo_l": (0.10, 0.80),
}
SIMILARITY_FLOOR, SIMILARITY_CEIL = SIMILARITY_BANDS[FACE_MODEL_NAME]

# The lowest score that counts as any resemblance at all, per model: below it a score
# is noise, not evidence. The review lists use it -- a face is "likely misassigned"
# only when someone else scores at least this (0.13 vs 0.14 is noise, not "closer to
# Nathan"), and "looks like nobody" when no one, its own person included, reaches it.
# buffalo_l: below 0.35 even good-quality genuine pairs are rare (3% in the benchmark).
PLAUSIBLE_MATCH_SIMILARITIES: dict[str, float] = {
    "buffalo_l": 0.35,
}
PLAUSIBLE_MATCH_SIMILARITY = PLAUSIBLE_MATCH_SIMILARITIES[FACE_MODEL_NAME]


def ui_threshold_to_similarity(
    ui_value: float,
    floor: float = SIMILARITY_FLOOR,
    ceil: float = SIMILARITY_CEIL,
) -> float:
    """Map a 0-100 UI similarity slider value (0 = least similar, 100 = most
    similar) onto the cosine scale, calibrated to the useful band [floor, ceil].
    Single place the user-facing scale is translated for both the assignment and
    review screens. Out-of-range clamps."""
    frac = max(0.0, min(100.0, float(ui_value))) / 100.0
    return floor + frac * (ceil - floor)


def similarity_to_ui_fraction(
    similarity: float,
    floor: float = SIMILARITY_FLOOR,
    ceil: float = SIMILARITY_CEIL,
) -> float:
    """Inverse of ui_threshold_to_similarity as an unrounded 0.0-1.0 fraction of the
    band, for a client that formats it as a percent itself. Clamped to [0, 1]."""
    frac = (similarity - floor) / (ceil - floor) if ceil > floor else 0.0
    return max(0.0, min(1.0, frac))


def similarity_to_ui_percent(
    similarity: float,
    floor: float = SIMILARITY_FLOOR,
    ceil: float = SIMILARITY_CEIL,
) -> int:
    """Inverse of ui_threshold_to_similarity: map a raw cosine similarity onto the
    0-100 UI percent over the same band, so a displayed score and the slider share
    one scale. Clamped to [0, 100]."""
    return round(similarity_to_ui_fraction(similarity, floor, ceil) * 100)


def reference_embedding_for_face(
    face: Face,
    stage_medoids: dict[str, np.ndarray],
    overall_medoid: np.ndarray | None,
    birthdate,
) -> np.ndarray | None:
    """The embedding a face should be scored against: the medoid of *its own* life
    stage.

    A person's face at 4 and at 40 are nearly orthogonal, so comparing a face to the
    wrong stage says little. The face's stage comes from life_stage(): the age implied
    by the birthdate and the photo's year when both are known, falling back to the
    face's own predicted age when they aren't.

    Falls back to the person's overall medoid when they have no embedding for that
    stage yet -- a stage the person simply has no assigned faces in. None when there's
    nothing to compare against at all.
    """
    stage = life_stage(
        birthdate,
        face.media_item.year if face.media_item else None,
        face.estimated_age,
    )
    return stage_medoids.get(stage, overall_medoid)


def calculate_similarity(person: Person, faces: list[Face]) -> dict[int, float]:
    """How much each face looks like `person`, scored against the medoid of the face's
    own life stage (see reference_embedding_for_face).

    A person with no gallery yet (nothing assigned to them) scores nothing -- an empty
    result, not a number. This used to fall back to the mean of the faces being scored,
    which measured them against *themselves*: every face came back looking like a
    strong match for a person it had never been compared to.
    """
    if len(faces) == 0:
        return {}
    stage_medoids = {
        person_embedding.life_stage: load_embedding(person_embedding.avg_embedding)
        for person_embedding in person.stage_embeddings
        if person_embedding.avg_embedding
    }
    overall_medoid = load_embedding(person.avg_embedding) if person.avg_embedding else None
    if not stage_medoids and overall_medoid is None:
        return {}

    birthdate = effective_birthdate(person)
    scores: dict[int, float] = {}
    for face in faces:
        reference = reference_embedding_for_face(face, stage_medoids, overall_medoid, birthdate)
        if reference is None:
            continue
        face_emb = load_embedding(face.embedding)
        scores[face.id] = float(cosine_similarity([face_emb], [reference])[0][0])
    return scores

def calculate_face_similarity(face: Face, people: list[Person]) -> dict[int, float] :
    def calculate_person_similarity(person: Person) -> float:
       face_emb = load_embedding(face.embedding)
       loaded_person_embeddings = [load_embedding(person_embedding.avg_embedding) for person_embedding in
                                   person.stage_embeddings]
       return max(
           cosine_similarity([face_emb], [person_embedding])[0][0]
           for person_embedding in loaded_person_embeddings
       )
    return { person.id: calculate_person_similarity(person) for person in people  }