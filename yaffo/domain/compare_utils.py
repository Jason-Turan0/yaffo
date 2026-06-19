import numpy as np
from yaffo.db.models import Face, Person
from sklearn.metrics.pairwise import cosine_similarity

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


def calculate_similarity(person: Person, faces: list[Face]) -> dict[int, float]:
    if len(faces) == 0: return {}
    loaded_person_embeddings = [load_embedding(person_embedding.avg_embedding) for person_embedding in person.stage_embeddings]
    if len(loaded_person_embeddings) == 0:
        loaded_person_embeddings = [np.mean([load_embedding(face.embedding) for face in faces], axis=0)]
    def calculate_similarity_for_face(face: Face) -> float:
       face_emb = load_embedding(face.embedding)
       if len(loaded_person_embeddings) == 0:
           return 0
       return max(
           cosine_similarity([face_emb], [person_embedding])[0][0]
           for person_embedding in loaded_person_embeddings
       )
    return { face.id: calculate_similarity_for_face(face) for face in faces  }

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