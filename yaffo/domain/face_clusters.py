"""Grouping unassigned faces by similarity, shared by the Faces page and the
assistant's suggest_face_clusters host function."""
import numpy as np
from sklearn.cluster import DBSCAN

# A cluster needs at least this many faces; smaller groups are noise.
MIN_CLUSTER_SIZE = 3


def cluster_faces(
    face_ids: list[int], embeddings: list[np.ndarray], min_similarity: float,
) -> list[tuple[int, list[int]]]:
    """(label, face ids) for each cluster, in DBSCAN label order; noise is dropped.

    ArcFace embeddings are L2-normalized, so faces cluster by cosine distance
    (1 - cos). `min_similarity` is the required cosine similarity (already scaled
    from the UI slider); eps is the complementary distance radius, so requiring
    more similarity tightens the clusters."""
    if not face_ids:
        return []
    labels = DBSCAN(eps=1.0 - min_similarity, min_samples=MIN_CLUSTER_SIZE,
                    metric="cosine").fit(np.array(embeddings)).labels_
    clusters: dict[int, list[int]] = {}
    for face_id, label in zip(face_ids, labels):
        if label != -1:
            clusters.setdefault(int(label), []).append(face_id)
    return list(clusters.items())
