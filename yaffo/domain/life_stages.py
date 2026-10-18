"""Life-stage bucketing for face embeddings.

A person's face changes far more between ages 0 and 19 than across all of
adulthood, so a single averaged embedding (or even a per-year one) blends together
faces that are nearly orthogonal in embedding space. We instead bucket a person's
faces into a few life stages and keep one representative embedding (a medoid) per
stage; matching takes the max cosine over those, so a baby photo matches the baby
medoid even though the adult medoid is far away.

Stages are coarse on purpose (tight when young, broad in adulthood). A face's stage
is its age when the photo was taken = photo year − birthdate year, using the
person's actual birthdate if set, otherwise the estimated one. With no birthdate at
all everything falls in UNKNOWN (a single bucket — graceful pre-birthdate fallback).

A photo taken before the birth year can't show the person, so its face lands in
BEFORE_BIRTH: a marker, never a gallery stage. It's most likely a wrong assignment
(or a wrong photo date), and guessing its stage from the face's predicted age would
build a medoid out of someone else -- e.g. a sibling's baby photo becoming a toddler's
whole "teen" stage.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Optional


@dataclass(frozen=True)
class LifeStage:
    name: str
    min_age: int  # inclusive
    max_age: int  # inclusive


LIFE_STAGES: list[LifeStage] = [
    LifeStage("baby", 0, 2),
    LifeStage("child", 3, 12),
    LifeStage("teen", 13, 19),
    LifeStage("adult", 20, 59),
    LifeStage("senior", 60, 200),
]

STAGE_UNKNOWN = "unknown"
STAGE_NAMES: list[str] = [s.name for s in LIFE_STAGES] + [STAGE_UNKNOWN]
# Not in STAGE_NAMES: no medoid is ever built for it (see the module docstring).
STAGE_BEFORE_BIRTH = "before_birth"


def life_stage_for_age(age_years: Optional[float]) -> str:
    if age_years is None or age_years < 0:
        return STAGE_UNKNOWN
    for stage in LIFE_STAGES:
        if stage.min_age <= age_years <= stage.max_age:
            return stage.name
    return STAGE_UNKNOWN


def life_stage_for_year(
    birthdate: Optional[date], photo_year: Optional[int], today: Optional[date] = None,
) -> str:
    """The life stage of a photo taken in `photo_year` for a person born on
    `birthdate`, or BEFORE_BIRTH for a photo from before the birth year. Year
    granularity is plenty given the stage widths. A photo dated after `today` (a bad
    date) is capped at the person's current age."""
    if birthdate is None or photo_year is None:
        return STAGE_UNKNOWN
    if photo_year < birthdate.year:
        return STAGE_BEFORE_BIRTH
    return life_stage_for_age(min(photo_year, (today or date.today()).year) - birthdate.year)


def life_stage(
    birthdate: Optional[date],
    photo_year: Optional[int],
    estimated_age: Optional[float] = None,
    today: Optional[date] = None,
) -> str:
    """A face's life stage. Prefers age from the (precise) photo date and the
    (robustly aggregated) birthdate; only when that can't be computed -- no
    birthdate, or the photo has no date -- does it fall back to the face's own
    noisy per-face predicted age, which still separates baby from adult. A photo from
    before the birth year stays BEFORE_BIRTH: the predicted age of a face that can't
    be this person says nothing about this person's stage.

    With a birthdate, no face can be older than the person is `today` (default: the
    current date): a 10-year-old has no teen or adult stage, however old an undated
    photo's predicted age says the face looks. Predicted ages run high on children, so
    the fallback is capped at the current age."""
    stage = life_stage_for_year(birthdate, photo_year, today)
    if stage == STAGE_UNKNOWN and estimated_age is not None:
        if birthdate is not None:
            current_age = (today or date.today()).year - birthdate.year
            estimated_age = min(estimated_age, current_age)
        return life_stage_for_age(estimated_age)
    return stage


def effective_birthdate(person) -> Optional[date]:
    """The birthdate to bucket by: the user-entered one wins; otherwise the
    estimated one; otherwise None (everything is UNKNOWN)."""
    return person.birthdate or person.estimated_birthdate
