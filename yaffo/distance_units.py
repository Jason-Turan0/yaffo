from __future__ import annotations

from dataclasses import dataclass

from flask_babel import lazy_gettext
from sqlalchemy.exc import OperationalError

from yaffo.db import db
from yaffo.db.models import ApplicationSettings

DISTANCE_UNIT_SETTING = "distance_unit"
DISTANCE_UNIT_MILES = "mi"
DISTANCE_UNIT_KILOMETERS = "km"
DEFAULT_DISTANCE_UNIT = DISTANCE_UNIT_MILES
KILOMETERS_PER_MILE = 1.609344


@dataclass(frozen=True)
class DistanceUnitOption:
    code: str
    label: str


def supported_distance_unit_options() -> list[DistanceUnitOption]:
    return [
        DistanceUnitOption(code=DISTANCE_UNIT_MILES, label=lazy_gettext("Miles")),
        DistanceUnitOption(code=DISTANCE_UNIT_KILOMETERS, label=lazy_gettext("Kilometers")),
    ]


def normalize_distance_unit(unit: str | None) -> str | None:
    if not unit:
        return None
    normalized = unit.strip().lower()
    return normalized if normalized in {DISTANCE_UNIT_MILES, DISTANCE_UNIT_KILOMETERS} else None


def get_saved_distance_unit(session=None) -> str:
    session = session or db.session
    try:
        row = session.query(ApplicationSettings).filter_by(name=DISTANCE_UNIT_SETTING).first()
    except OperationalError:
        return DEFAULT_DISTANCE_UNIT
    return normalize_distance_unit(row.value if row else None) or DEFAULT_DISTANCE_UNIT


def set_distance_unit(unit: str) -> bool:
    normalized = normalize_distance_unit(unit)
    if normalized is None:
        return False
    row = db.session.query(ApplicationSettings).filter_by(name=DISTANCE_UNIT_SETTING).first()
    if row is None:
        db.session.add(ApplicationSettings(name=DISTANCE_UNIT_SETTING, type="string", value=normalized))
    else:
        row.value = normalized
    db.session.commit()
    return True


def kilometers_per_unit(unit: str) -> float:
    return {
        DISTANCE_UNIT_MILES: KILOMETERS_PER_MILE,
        DISTANCE_UNIT_KILOMETERS: 1.0,
    }[normalize_distance_unit(unit) or DEFAULT_DISTANCE_UNIT]


def distance_to_kilometers(value: float, unit: str) -> float:
    return value * kilometers_per_unit(unit)


def kilometers_to_distance(value_kilometers: float, unit: str) -> float:
    return value_kilometers / kilometers_per_unit(unit)
