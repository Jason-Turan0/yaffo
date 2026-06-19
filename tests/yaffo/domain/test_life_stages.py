from datetime import date
from types import SimpleNamespace

import pytest

from yaffo.domain.life_stages import (
    STAGE_UNKNOWN, effective_birthdate, life_stage, life_stage_for_age, life_stage_for_year,
)

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("age,stage", [
    (0, "baby"), (2, "baby"),
    (3, "child"), (12, "child"),
    (13, "teen"), (19, "teen"),
    (20, "adult"), (59, "adult"),
    (60, "senior"), (200, "senior"),
    (None, STAGE_UNKNOWN), (-1, STAGE_UNKNOWN),
])
def test_life_stage_for_age_boundaries(age, stage):
    assert life_stage_for_age(age) == stage


def test_life_stage_for_year_uses_birth_year():
    bd = date(2010, 6, 1)
    assert life_stage_for_year(bd, 2011) == "baby"     # age ~1
    assert life_stage_for_year(bd, 2020) == "child"    # age ~10
    assert life_stage_for_year(bd, 2025) == "teen"     # age ~15


def test_life_stage_for_year_unknown_without_inputs():
    assert life_stage_for_year(None, 2020) == STAGE_UNKNOWN
    assert life_stage_for_year(date(2000, 1, 1), None) == STAGE_UNKNOWN


def test_life_stage_prefers_birthdate_over_estimated_age():
    # birthdate-derived age (3 -> child) wins; the noisy per-face age is ignored
    assert life_stage(date(2010, 1, 1), 2013, estimated_age=40) == "child"


def test_life_stage_falls_back_to_estimated_age():
    # no birthdate -> use the face's own predicted age
    assert life_stage(None, 2013, estimated_age=1) == "baby"
    # birthdate but the photo has no date -> also fall back
    assert life_stage(date(2010, 1, 1), None, estimated_age=1) == "baby"


def test_life_stage_unknown_when_nothing_available():
    assert life_stage(None, 2013, estimated_age=None) == STAGE_UNKNOWN
    assert life_stage(None, None, None) == STAGE_UNKNOWN


def test_effective_birthdate_prefers_actual():
    actual, estimated = date(1990, 1, 1), date(1992, 1, 1)
    assert effective_birthdate(SimpleNamespace(birthdate=actual, estimated_birthdate=estimated)) == actual
    assert effective_birthdate(SimpleNamespace(birthdate=None, estimated_birthdate=estimated)) == estimated
    assert effective_birthdate(SimpleNamespace(birthdate=None, estimated_birthdate=None)) is None
