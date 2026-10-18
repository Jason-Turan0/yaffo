from datetime import date
from types import SimpleNamespace

import pytest

from yaffo.domain.life_stages import (
    STAGE_BEFORE_BIRTH, STAGE_UNKNOWN, effective_birthdate, life_stage, life_stage_for_age, life_stage_for_year,
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


def test_photo_before_the_birth_year_is_before_birth_not_a_guessed_stage():
    """Born 2019, photographed 2016: the face can't be this person, so its predicted
    age (17 here, a real case) must not file it as their "teen" stage."""
    assert life_stage_for_year(date(2019, 9, 6), 2016) == STAGE_BEFORE_BIRTH
    assert life_stage(date(2019, 9, 6), 2016, estimated_age=17) == STAGE_BEFORE_BIRTH
    assert life_stage(date(2019, 9, 6), 2019, estimated_age=17) == "baby"  # birth year itself is age 0


def test_an_undated_face_cannot_be_older_than_the_person_is_today():
    """Chase, born 2016, is 10 in 2026: an undated photo whose predicted age is 24
    is filed under his current stage (child), never a teen or adult stage he can't
    have reached."""
    today = date(2026, 10, 18)
    chase = date(2016, 1, 28)
    assert life_stage(chase, None, estimated_age=24, today=today) == "child"
    assert life_stage(chase, None, estimated_age=1, today=today) == "baby"  # younger guesses stand
    assert life_stage(None, None, estimated_age=24, today=today) == "adult"  # no birthdate, no cap


def test_a_photo_dated_in_the_future_is_capped_at_the_current_age():
    today = date(2026, 10, 18)
    assert life_stage(date(2016, 1, 28), 2040, today=today) == "child"
