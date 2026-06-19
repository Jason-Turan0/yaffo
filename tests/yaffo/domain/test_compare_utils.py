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
