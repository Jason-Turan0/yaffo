"""Declarative config schemas for system automations.

A system automation can expose runtime-tunable settings stored in
`Automation.config` (JSON). This module is the single source of truth for which
handler exposes which fields, read in two places that can't diverge: the
automations route (validation + the render context the Configure modal needs) and
the task that runs the automation (reading the live value). Custom automations
carry no config schema -- they encode their behaviour in their Starlark.

Add a tunable setting = add one `ConfigField` to the handler's list.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from yaffo.db.models import (
    Automation,
    AUTOMATION_HANDLER_AUTO_ASSIGN_FACES,
    AUTO_ASSIGN_FACES_DEFAULT_THRESHOLD,
    AUTOMATION_HANDLER_EXPORT_PHOTO_TAG,
    AUTOMATION_HANDLER_ASSIGN_LOCATION_NAME,
    AUTOMATION_HANDLER_GEOTAG_FROM_NEIGHBORS,
    GEOTAG_FROM_NEIGHBORS_DEFAULT_MINUTES,
    AUTOMATION_HANDLER_CLASSIFY_LABELS,
    CLASSIFY_LABELS_DEFAULT_THRESHOLD,
    CLASSIFY_LABELS_DEFAULT_MAX,
)


@dataclass(frozen=True)
class ConfigField:
    """A single runtime setting on a system automation, written to Automation.config.

    `type` drives both the input rendered in the Configure modal and the coercion
    applied on save: 'float'/'int' -> number input, 'string' -> text input,
    'bool' -> checkbox, 'distance' -> numeric input plus mi/km unit selector.
    min/max/step apply to the numeric types only."""
    key: str
    label: str
    type: str
    default: str | float | int | bool
    help: Optional[str] = None
    min: Optional[float] = None
    max: Optional[float] = None
    step: Optional[float] = None
    required: bool = True
    unit_key: str | None = None


AUTOMATION_CONFIG: dict[str, list[ConfigField]] = {
    AUTOMATION_HANDLER_AUTO_ASSIGN_FACES: [
        ConfigField(
            key="threshold",
            label="Match threshold",
            help=(
                "A detected face is assigned only when exactly one person matches at "
                "or above this similarity (0 = least similar, 100 = most similar) "
                "Higher is stricter: fewer, more confident assignments."
            ),
            min=0,
            max=100,
            step=1,
            default=AUTO_ASSIGN_FACES_DEFAULT_THRESHOLD,
            type='int'
        ),
    ],
    AUTOMATION_HANDLER_EXPORT_PHOTO_TAG: [
        ConfigField(
            key="export_location_tag_enabled",
            label="Export Location Tags",
            default=False,
            type='bool'
        ),
        ConfigField(
            key="export_people_tag_enabled",
            label="Export People Tags",
            default=False,
            type='bool'
        ),
        ConfigField(
            key="export_labels_enabled",
            label="Export Labels",
            help="Write the photo's classification labels into the file's keywords.",
            default=False,
            type='bool'
        ),
        ConfigField(
            key="export_custom_tags_enabled",
            label="Export Custom Tags",
            help="Write the photo's manual tags (name, or name: value) into the file's keywords.",
            default=False,
            type='bool'
        ),
        ConfigField(
            key="export_favorite_enabled",
            label="Export Favorite",
            help="Write a \"Favorite\" keyword into the file when the photo is marked a favorite.",
            default=False,
            type='bool'
        ),
    ],
    AUTOMATION_HANDLER_ASSIGN_LOCATION_NAME: [
        ConfigField(
            key="reuse_nearby_enabled",
            label="Reuse a nearby photo's name",
            help=(
                "Copy the location name of the closest already-named photo within "
                "the radius below. Free, offline, and keeps your own naming."
            ),
            default=True,
            type='bool',
        ),
        ConfigField(
            key="nearby_radius",
            label="Nearby radius",
            help=(
                "How close an already-named photo must be to copy its name. Larger "
                "values reuse names more aggressively and make fewer online lookups."
            ),
            min=0.01,
            max=50,
            step=0.1,
            default=10,
            type='distance',
            unit_key="nearby_radius_unit",
        ),
        ConfigField(
            key="reverse_geocode_enabled",
            label="Look up name online (OpenStreetMap)",
            help=(
                "When no nearby photo is named, reverse-geocode the coordinates via "
                "OpenStreetMap Nominatim (throttled to ~1 request/second)."
            ),
            default=True,
            type='bool',
        ),
        ConfigField(
            key="overwrite_existing",
            label="Overwrite existing location names",
            help="When off, photos that already have a location name are left untouched.",
            default=False,
            type='bool',
        ),
    ],
    AUTOMATION_HANDLER_GEOTAG_FROM_NEIGHBORS: [
        ConfigField(
            key="max_minutes",
            label="Time window (minutes)",
            help=(
                "A photo with no GPS borrows the coordinates of the closest-in-time "
                "GPS-tagged photo, but only if it was taken within this many minutes "
                "(so coordinates aren't copied across a long gap / a different place)."
            ),
            min=1,
            max=1440,
            step=1,
            default=GEOTAG_FROM_NEIGHBORS_DEFAULT_MINUTES,
            type='int',
        ),
    ],
    AUTOMATION_HANDLER_CLASSIFY_LABELS: [
        ConfigField(
            key="confidence_threshold",
            label="Confidence threshold",
            help=(
                "A photo gets a label only when the CLIP image–text similarity is at or above this confidence "
                "(0 = least similar, 100 = most similar) Higher is stricter: fewer, more confident labels."
            ),
            min=0.0,
            max=100.0,
            step=1,
            default=CLASSIFY_LABELS_DEFAULT_THRESHOLD,
            type='int',
        ),
        ConfigField(
            key="max_labels",
            label="Max labels per photo",
            help="At most this many labels are kept per photo (the highest-scoring ones).",
            min=1,
            max=20,
            step=1,
            default=CLASSIFY_LABELS_DEFAULT_MAX,
            type='int',
        ),
    ],
}


def config_fields_for(automation: Automation) -> list[ConfigField]:
    """The config schema for an automation (empty if it exposes none)."""
    return AUTOMATION_CONFIG.get(automation.handler or "", [])


def config_value(automation: Automation, field: ConfigField) -> float:
    """The live value of a config field: the stored value, else the default."""
    stored = (automation.config or {}).get(field.key)
    return stored if isinstance(stored, (str, bool, int, float)) else field.default
