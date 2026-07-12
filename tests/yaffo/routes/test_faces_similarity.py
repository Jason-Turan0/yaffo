import numpy as np

from yaffo.routes.faces import _centroid_similarities


def _unit(vector: list[float]) -> np.ndarray:
    array = np.array(vector, dtype=np.float64)
    return array / np.linalg.norm(array)


def test_identical_embeddings_all_score_one():
    embedding = _unit([0.6, 0.8, 0.0])

    similarities = _centroid_similarities([embedding, embedding, embedding])

    assert similarities == [1.0, 1.0, 1.0]


def test_outlier_scores_below_the_tight_members():
    tight = _unit([1.0, 0.0, 0.0])
    near = _unit([0.99, 0.14, 0.0])
    outlier = _unit([0.5, 0.87, 0.0])

    tight_score, near_score, outlier_score = _centroid_similarities([tight, near, outlier])

    assert outlier_score < tight_score
    assert outlier_score < near_score
    assert 0.0 <= outlier_score <= 1.0


def test_antipodal_members_cancel_to_zero():
    embedding = _unit([1.0, 0.0, 0.0])

    similarities = _centroid_similarities([embedding, -embedding])

    assert similarities == [0.0, 0.0]
