import importlib
import json
import sqlite3

import pytest

pytestmark = pytest.mark.unit


def test_migration_converts_legacy_radius_meters_to_kilometers_config():
    migration = importlib.import_module(
        "yaffo.scripts.db.migrations.005_MIGRATION_20260626_normalize_assign_location_radius_config"
    )
    connection = sqlite3.connect(":memory:")
    connection.execute(
        "CREATE TABLE automations (id INTEGER PRIMARY KEY, slug TEXT, config JSON)"
    )
    connection.execute(
        "INSERT INTO automations (slug, config) VALUES (?, ?)",
        (
            "assign_location_name",
            json.dumps({
                "reuse_nearby_enabled": True,
                "nearby_radius_meters": 2500,
                "reverse_geocode_enabled": False,
            }),
        ),
    )

    migration.migrate(connection)

    config = json.loads(connection.execute("SELECT config FROM automations").fetchone()[0])
    assert config["nearby_radius"] == 2.5
    assert config["nearby_radius_unit"] == "km"
    assert config["nearby_radius_kilometers"] == 2.5
    assert "nearby_radius_meters" not in config


def test_migration_removes_legacy_radius_when_new_config_exists():
    migration = importlib.import_module(
        "yaffo.scripts.db.migrations.005_MIGRATION_20260626_normalize_assign_location_radius_config"
    )
    connection = sqlite3.connect(":memory:")
    connection.execute(
        "CREATE TABLE automations (id INTEGER PRIMARY KEY, slug TEXT, config JSON)"
    )
    connection.execute(
        "INSERT INTO automations (slug, config) VALUES (?, ?)",
        (
            "assign_location_name",
            json.dumps({
                "nearby_radius": 3,
                "nearby_radius_unit": "mi",
                "nearby_radius_kilometers": 4.828032,
                "nearby_radius_meters": 2500,
            }),
        ),
    )

    migration.migrate(connection)

    config = json.loads(connection.execute("SELECT config FROM automations").fetchone()[0])
    assert config["nearby_radius"] == 3
    assert config["nearby_radius_unit"] == "mi"
    assert config["nearby_radius_kilometers"] == 4.828032
    assert "nearby_radius_meters" not in config
