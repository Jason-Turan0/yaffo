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


def calculate_similarity(person: Person, faces: list[Face]) -> dict[int, float]:
    if len(faces) == 0: return {}
    loaded_person_embeddings = [load_embedding(person_embedding.avg_embedding) for person_embedding in person.embeddings_by_year]
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
                                   person.embeddings_by_year]
       return max(
           cosine_similarity([face_emb], [person_embedding])[0][0]
           for person_embedding in loaded_person_embeddings
       )
    return { person.id: calculate_person_similarity(person) for person in people  }