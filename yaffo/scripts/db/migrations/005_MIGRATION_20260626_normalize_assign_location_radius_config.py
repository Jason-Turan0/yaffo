"""Normalize assign-location-name radius config to kilometers.

The runner manages the transaction and records this migration; do not open a
connection or commit here.
"""
import json
import sqlite3


def _normalized_config(raw_config: str | None) -> str | None:
    if not raw_config:
        return None
    try:
        config = json.loads(raw_config)
    except json.JSONDecodeError:
        return None
    if not isinstance(config, dict) or "nearby_radius_meters" not in config:
        return None

    legacy_meters = config.pop("nearby_radius_meters")
    if "nearby_radius_kilometers" not in config and isinstance(legacy_meters, (int, float)):
        radius_kilometers = float(legacy_meters) / 1000.0
        config["nearby_radius"] = radius_kilometers
        config["nearby_radius_unit"] = "km"
        config["nearby_radius_kilometers"] = radius_kilometers

    return json.dumps(config, sort_keys=True)


def migrate(conn: sqlite3.Connection) -> None:
    for automation_id, config in conn.execute(
        "SELECT id, config FROM automations WHERE slug = 'assign_location_name'"
    ).fetchall():
        normalized = _normalized_config(config)
        if normalized is not None:
            conn.execute(
                "UPDATE automations SET config = ? WHERE id = ?",
                (normalized, automation_id),
            )
