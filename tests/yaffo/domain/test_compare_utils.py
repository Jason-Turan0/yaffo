"""ui_threshold_to_similarity / similarity_to_ui_percent: 0-100 UI scale <-> the
calibrated cosine band [floor, ceil]."""
import pytest

from yaffo.domain.compare_utils import (
    ui_threshold_to_similarity,
    similarity_to_ui_percent,
    DEFAULT_SIMILARITY_FLOOR,
    DEFAULT_SIMILARITY_CEIL,
)

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("ui_value, expected", [
    (0, 0.40),      # least similar -> band floor
    (50, 0.55),     # midpoint
    (100, 0.70),    # most similar -> band ceil
])
def test_maps_ui_value_onto_the_given_band(ui_value, expected):
    assert ui_threshold_to_similarity(ui_value, 0.40, 0.70) == pytest.approx(expected)


def test_defaults_to_the_documented_band():
    assert ui_threshold_to_similarity(0) == pytest.approx(DEFAULT_SIMILARITY_FLOOR)
    assert ui_threshold_to_similarity(100) == pytest.approx(DEFAULT_SIMILARITY_CEIL)


@pytest.mark.parametrize("ui_value", [-5, 150])
def test_ui_value_clamps_to_the_band(ui_value):
    value = ui_threshold_to_similarity(ui_value, 0.40, 0.70)
    assert 0.40 <= value <= 0.70


@pytest.mark.parametrize("similarity, expected", [
    (0.40, 0),      # at/below floor -> 0%
    (0.55, 50),     # midpoint
    (0.70, 100),    # at/above ceil -> 100%
    (0.20, 0),      # below floor clamps
    (0.95, 100),    # above ceil clamps
])
def test_similarity_to_percent_is_the_inverse(similarity, expected):
    assert similarity_to_ui_percent(similarity, 0.40, 0.70) == expected


def test_roundtrips_ui_to_similarity_and_back():
    for ui in (0, 25, 50, 75, 100):
        cosine = ui_threshold_to_similarity(ui, 0.40, 0.70)
        assert similarity_to_ui_percent(cosine, 0.40, 0.70) == ui


def test_degenerate_band_does_not_divide_by_zero():
    assert similarity_to_ui_percent(0.5, 0.5, 0.5) == 0


def test_higher_slider_demands_more_similarity():
    assert ui_threshold_to_similarity(20) < ui_threshold_to_similarity(80)


# calculate_similarity scores a face against the medoid of its OWN life stage. It's the
# rule the assignment screen's suggestions and the auto-assign automation both use, and
# person_repository.recompute_person_similarities mirrors it for the stored cache — the
# three have to agree or the review screen sorts by one meaning and displays another.

from datetime import date
from types import SimpleNamespace

import numpy as np

from yaffo.domain.compare_utils import calculate_similarity, serialize_embedding


def _vec(*values) -> np.ndarray:
    v = np.zeros(512, dtype=np.float32)
    for i, x in enumerate(values):
        v[i] = x
    return v / np.linalg.norm(v)


def _person(birthdate, stages: dict, overall=None):
    return SimpleNamespace(
        birthdate=birthdate,
        estimated_birthdate=None,
        avg_embedding=serialize_embedding(overall) if overall is not None else None,
        stage_embeddings=[
            SimpleNamespace(life_stage=stage, avg_embedding=serialize_embedding(vec))
            for stage, vec in stages.items()
        ],
    )


def _face(face_id, vec, year, estimated_age=None):
    return SimpleNamespace(
        id=face_id,
        embedding=serialize_embedding(vec),
        estimated_age=estimated_age,
        media_item=SimpleNamespace(year=year),
    )


def test_scores_against_the_faces_own_stage_not_the_best_matching_stage():
    person = _person(date(2000, 1, 1), {"baby": _vec(1, 0), "adult": _vec(0, 1)})
    # A 2001 photo => baby stage, but the face leans toward the adult medoid.
    face = _face(1, _vec(0.2, 0.98), year=2001)

    scores = calculate_similarity(person, [face])

    assert scores[1] == pytest.approx(0.2, abs=0.02)   # the baby medoid, its own stage


def test_falls_back_to_the_overall_medoid_for_a_stage_the_person_has_no_faces_in():
    person = _person(date(1950, 1, 1), {"adult": _vec(1, 0)}, overall=_vec(1, 0))
    senior = _face(1, _vec(1, 0), year=2020)  # 70 years old: no senior medoid exists

    assert calculate_similarity(person, [senior])[1] == pytest.approx(1.0, abs=1e-6)


def test_uses_the_predicted_age_when_the_birthdate_or_photo_year_is_missing():
    """No birthdate to date the photo against, so the face's own predicted age is what
    places it — the fallback life_stage() already encodes."""
    person = _person(None, {"baby": _vec(1, 0), "adult": _vec(0, 1)})
    baby = _face(1, _vec(1, 0), year=None, estimated_age=1)
    adult = _face(2, _vec(0, 1), year=None, estimated_age=40)

    scores = calculate_similarity(person, [baby, adult])

    assert scores[1] == pytest.approx(1.0, abs=1e-6)
    assert scores[2] == pytest.approx(1.0, abs=1e-6)


def test_a_person_with_no_gallery_scores_nothing():
    assert calculate_similarity(_person(None, {}), [_face(1, _vec(1, 0), year=2001)]) == {}
