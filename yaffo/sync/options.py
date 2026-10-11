"""Adapter options: the declaration format (`ADAPTER["options"]`) and validation of
the values a sync stores in `config.options`.

Each option has a `type`, a `label`, and optionally `help`, `default`, `required`,
and (for `select`) `choices: [{value, label}]`. Built-in adapters may also declare
`needed` (must be set before the sync is turned on, but may be empty while saving,
for a value picked after another one) and `depends_on` (the option this one is
picked inside: it's cleared when that one changes). `local_folder` is host-native only
(built-in adapters): an absolute folder path picked with the native folder picker.

A remote value is `{id, label}`, picked from the service. A `remote_collection` that
declares `create: True` may instead hold `{label, new: True}`: a collection the user
named, which the adapter creates on its first run.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

OPTION_STRING = "string"
OPTION_INT = "int"
OPTION_BOOL = "bool"
OPTION_SELECT = "select"
OPTION_LOCAL_FOLDER = "local_folder"
OPTION_REMOTE_FOLDER = "remote_folder"
OPTION_REMOTE_COLLECTION = "remote_collection"
OPTION_TYPES = (OPTION_STRING, OPTION_INT, OPTION_BOOL, OPTION_SELECT, OPTION_LOCAL_FOLDER,
                OPTION_REMOTE_FOLDER, OPTION_REMOTE_COLLECTION)

MAX_STRING_LENGTH = 1000
MAX_NEW_NAME_LENGTH = 200


class OptionError(ValueError):
    """An option value is missing or invalid. `key` names the option."""

    def __init__(self, key: str, code: str, message: str):
        super().__init__(message)
        self.key = key
        self.code = code


def _coerce(key: str, spec: dict, value: Any) -> Any:
    kind = spec["type"]
    if kind == OPTION_BOOL:
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.lower() in ("true", "on", "1", "false", "off", "0", ""):
            return value.lower() in ("true", "on", "1")
        raise OptionError(key, "not_bool", f"{key} must be true or false")
    if kind == OPTION_INT:
        try:
            return int(value)
        except (TypeError, ValueError):
            raise OptionError(key, "not_int", f"{key} must be a whole number") from None
    if kind == OPTION_SELECT:
        allowed = [choice["value"] for choice in spec.get("choices", [])]
        if value not in allowed:
            raise OptionError(key, "not_a_choice", f"{key} must be one of {allowed}")
        return value
    if kind == OPTION_STRING:
        if not isinstance(value, str) or len(value) > MAX_STRING_LENGTH:
            raise OptionError(key, "not_string", f"{key} must be text")
        return value
    if kind == OPTION_LOCAL_FOLDER:
        if not isinstance(value, str) or not value.strip():
            raise OptionError(key, "required", f"{key} is required")
        path = Path(value).expanduser()
        if not path.is_absolute():
            raise OptionError(key, "not_absolute", f"{key} must be an absolute folder path")
        return str(path)
    if kind in (OPTION_REMOTE_FOLDER, OPTION_REMOTE_COLLECTION):
        if isinstance(value, dict) and set(value) == {"id", "label"} \
                and isinstance(value["id"], str) and isinstance(value["label"], str):
            return dict(value)
        if spec.get("create") and isinstance(value, dict) and set(value) == {"label", "new"} \
                and value["new"] is True and isinstance(value["label"], str):
            name = value["label"].strip()
            if not name or len(name) > MAX_NEW_NAME_LENGTH:
                raise OptionError(key, "bad_new_name", f"{key} needs a name of at most {MAX_NEW_NAME_LENGTH} characters")
            return {"label": name, "new": True}
        raise OptionError(key, "not_remote_value", f"{key} must be picked from the service")
    raise OptionError(key, "unknown_type", f"{key} has an unknown option type {kind!r}")


def _missing(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def validate_options(declared: dict[str, dict], values: dict[str, Any]) -> dict[str, Any]:
    """The stored form of `values` for an adapter declaring `declared`: unknown keys
    rejected, defaults filled in, types coerced. Raises OptionError."""
    unknown = sorted(set(values) - set(declared))
    if unknown:
        raise OptionError(unknown[0], "unknown_option", f"unknown option {unknown[0]!r}")
    result: dict[str, Any] = {}
    for key, spec in declared.items():
        value = values.get(key)
        if _missing(value) and spec["type"] != OPTION_BOOL:
            if "default" in spec:
                result[key] = spec["default"]
                continue
            if spec.get("required"):
                raise OptionError(key, "required", f"{key} is required")
            result[key] = None
            continue
        if value is None:
            value = spec.get("default", False)
        result[key] = _coerce(key, spec, value)
    return result


def option_defaults(declared: dict[str, dict]) -> dict[str, Any]:
    """The values a new sync starts with: each option's default, or None."""
    return {key: spec.get("default", False if spec["type"] == OPTION_BOOL else None)
            for key, spec in declared.items()}
