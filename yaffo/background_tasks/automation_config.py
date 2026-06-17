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

from yaffo.db.models import (
    Automation,
    AUTOMATION_HANDLER_AUTO_ASSIGN_FACES,
    AUTO_ASSIGN_FACES_DEFAULT_THRESHOLD,
)


@dataclass(frozen=True)
class ConfigField:
    """A single numeric setting on a system automation, rendered as a number input
    and validated against [min, max] before being written to Automation.config."""
    key: str
    label: str
    help: str
    min: float
    max: float
    step: float
    default: float


AUTOMATION_CONFIG: dict[str, list[ConfigField]] = {
    AUTOMATION_HANDLER_AUTO_ASSIGN_FACES: [
        ConfigField(
            key="threshold",
            label="Match threshold",
            help=(
                "A detected face is assigned only when exactly one person matches at "
                "or above this similarity (0–1). Higher is stricter — fewer, more "
                "confident assignments."
            ),
            min=0.5,
            max=1.0,
            step=0.01,
            default=AUTO_ASSIGN_FACES_DEFAULT_THRESHOLD,
        ),
    ],
}


def config_fields_for(automation: Automation) -> list[ConfigField]:
    """The config schema for an automation (empty if it exposes none)."""
    return AUTOMATION_CONFIG.get(automation.handler or "", [])


def config_value(automation: Automation, field: ConfigField) -> float:
    """The live value of a config field: the stored value, else the default."""
    stored = (automation.config or {}).get(field.key)
    return stored if isinstance(stored, (int, float)) else field.default
