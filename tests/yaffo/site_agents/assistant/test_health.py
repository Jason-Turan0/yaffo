"""Health checks, one fixture per known incident."""
import pytest

from yaffo.site_agents.assistant.tool_providers.diagnostics import health
from yaffo.site_agents.assistant.tool_providers.diagnostics.health import OK, PROBLEM, WARNING

pytestmark = pytest.mark.unit


def _levels(findings):
    return [f.level for f in findings]


def test_unmounted_media_dir_is_a_problem():
    findings = health.check_media_dir("/Volumes/Photos", {"exists": False})
    assert _levels(findings) == [PROBLEM]
    assert "not mounted" in findings[0].message and findings[0].doc


def test_media_dir_probe_and_space():
    facts = {"exists": True, "readable": True, "total_bytes": 1000 * 1024 ** 3, "free_bytes": 1024 ** 3}
    assert _levels(health.check_media_dir("/m", facts, {"responded": False})) == [PROBLEM, WARNING]
    assert _levels(health.check_media_dir("/m", facts | {"free_bytes": 500 * 1024 ** 3},
                                          {"responded": True, "seconds": 3.0})) == [WARNING]
    assert _levels(health.check_media_dir("/m", facts | {"free_bytes": 500 * 1024 ** 3},
                                          {"responded": True, "seconds": 0.01})) == [OK]


def test_thumbnail_dir_inside_library_without_marker():
    assert _levels(health.check_thumbnail_dir(True, True, True, False)) == [PROBLEM]
    assert _levels(health.check_thumbnail_dir(True, True, True, True)) == [OK]
    assert _levels(health.check_thumbnail_dir(True, False, False, False)) == [PROBLEM]


def test_face_states():
    assert _levels(health.check_faces(3, 0, 0, 0)) == [PROBLEM]
    # PROCESSING is only stuck when no face task is queued.
    assert _levels(health.check_faces(0, 5, 0, 0)) == [PROBLEM]
    assert _levels(health.check_faces(0, 5, 1, 0)) == [OK]
    assert _levels(health.check_faces(0, 0, 0, 2)) == [WARNING]


def test_dates_year_5000_and_undated_share():
    assert _levels(health.check_dates(1, 0, 100)) == [WARNING]
    assert _levels(health.check_dates(0, 30, 100)) == [WARNING]
    assert _levels(health.check_dates(0, 10, 100)) == [OK]


def test_worker_heartbeat():
    assert _levels(health.check_worker(None, 2)) == [PROBLEM]
    assert _levels(health.check_worker(None, 0)) == [WARNING]
    assert _levels(health.check_worker(600, 0)) == [PROBLEM]
    assert _levels(health.check_worker(3, 1)) == [OK]


def test_install_and_migrations():
    assert _levels(health.check_migrations(["012_x"])) == [PROBLEM]
    assert health.check_install(100.0, 200.0)[0].level == WARNING
    assert health.check_install(300.0, 200.0) == []


def test_failing_automation_needs_three_straight_failures():
    assert _levels(health.check_automations({"Sync": ["FAILED", "FAILED", "FAILED"]})) == [WARNING]
    assert _levels(health.check_automations({"Sync": ["FAILED", "COMPLETED", "FAILED"]})) == [OK]
    assert _levels(health.check_automations({"New": ["FAILED"]})) == [OK]


def test_overall_takes_the_worst():
    findings = health.check_faces(0, 0, 0, 2) + health.check_migrations([])
    assert health.overall(findings) == WARNING
