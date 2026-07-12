import numpy as np
from yaffo.db.models import Face, Person
from sklearn.metrics.pairwise import cosine_similarity

from yaffo.domain.life_stages import effective_birthdate, life_stage

def load_embedding(blob: bytes) -> np.ndarray:
    """Deserialize a stored face embedding. ArcFace embeddings are 512-d float32
    (see yaffo/utils/face_analysis); dimensionality is left implicit so the same
    code works regardless of model."""
    return np.frombuffer(blob, dtype=np.float32).reshape(-1)


def serialize_embedding(arr: np.ndarray) -> bytes:
    """Serialize an embedding for storage. Always float32 so reads via
    load_embedding are consistent (np.mean etc. otherwise widen to float64)."""
    return np.asarray(arr, dtype=np.float32).tobytes()


# Stored face<->person cosine similarities don't usefully span [0, 1]: real matches
# crowd into a band well above 0 (measured on the live gallery 2026-06-19: p5=0.31,
# median=0.66, p95=0.85). So the 0-100 UI scale is calibrated onto that band, not
# raw cosine -- otherwise the bottom third of every slider is dead and every score
# reads "low". The band is data-driven (get_similarity_bounds queries the actual
# people_face.similarity percentiles); these constants are only the cold-start
# fallback for when there aren't enough assignments yet to measure it.
DEFAULT_SIMILARITY_FLOOR = 0.35
DEFAULT_SIMILARITY_CEIL = 0.90


def ui_threshold_to_similarity(
    ui_value: float,
    floor: float = DEFAULT_SIMILARITY_FLOOR,
    ceil: float = DEFAULT_SIMILARITY_CEIL,
) -> float:
    """Map a 0-100 UI similarity slider value (0 = least similar, 100 = most
    similar) onto the cosine scale, calibrated to the useful band [floor, ceil].
    Single place the user-facing scale is translated for both the assignment and
    review screens. Out-of-range clamps."""
    frac = max(0.0, min(100.0, float(ui_value))) / 100.0
    return floor + frac * (ceil - floor)


def similarity_to_ui_percent(
    similarity: float,
    floor: float = DEFAULT_SIMILARITY_FLOOR,
    ceil: float = DEFAULT_SIMILARITY_CEIL,
) -> int:
    """Inverse of ui_threshold_to_similarity: map a raw cosine similarity onto the
    0-100 UI percent over the same band, so a displayed score and the slider share
    one scale. Clamped to [0, 100]."""
    frac = (similarity - floor) / (ceil - floor) if ceil > floor else 0.0
    return round(max(0.0, min(1.0, frac)) * 100)


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