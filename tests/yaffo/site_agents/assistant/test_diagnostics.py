"""Diagnostic tools against a real database, queue store, media dir and logs."""
import json
import time
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from yaffo.db import db
from yaffo.db.models import (
    ApplicationSettings,
    Automation,
    Face,
    FACE_STATUS_ASSIGNED,
    FACE_STATUS_IGNORED,
    FACE_STATUS_PROCESSING,
    Job,
    KnownDevice,
    MediaItem,
    Person,
    PersonFace,
    ShareGrant,
    Tag,
)
from yaffo.site_agents.assistant.tool_providers.diagnostics import diagnostics as diag
from yaffo.site_agents.assistant.tool_providers.diagnostics.diagnostics import DiagnosticsToolProvider, tool_names
from yaffo.site_agents.assistant.tool_providers.diagnostics.fs import AssistantFS
from yaffo.site_agents.assistant.redact import Redactor
from yaffo import process_status
from yaffo.process_status import read_status, write_status
from yaffo.taskq.store import Store

pytestmark = pytest.mark.unit

ALL = frozenset({"logs", "library", "files", "jobs"})


@pytest.fixture
def env(tmp_path, monkeypatch):
    media = tmp_path / "home" / "Pictures"
    (media / "2019").mkdir(parents=True)
    (media / "2019" / "a.jpg").write_bytes(b"jpg")
    data = tmp_path / "data"
    data.mkdir()
    engine = create_engine(f"sqlite:///{tmp_path / 'lib.db'}")
    db.metadata.create_all(engine)
    session = Session(engine)
    session.add(ApplicationSettings(name="media_dirs", type="json",
                                    value=json.dumps([{"id": "m1", "path": str(media)}])))
    alice = Person(id=1, name="Alice Smith")
    session.add_all([
        alice,
        MediaItem(id=1, full_file_path=str(media / "2019" / "a.jpg"), date_taken="2019-08-01T10:00:00",
                  year=2019, status="INDEXED", media_type="photo"),
        MediaItem(id=2, full_file_path=str(media / "2019" / "gone.jpg"), date_taken="5000-01-01T00:00:00",
                  year=5000, status="INDEXED", media_type="photo"),
        MediaItem(id=3, full_file_path=str(media / "nodate.jpg"), status="IMPORTED", media_type="photo"),
        Face(id=10, media_item_id=1, status=FACE_STATUS_PROCESSING),
        Face(id=11, media_item_id=1, status=FACE_STATUS_IGNORED),
        Face(id=12, media_item_id=1, status=FACE_STATUS_ASSIGNED),
        Tag(media_item_id=1, tag_name="trip", tag_value=None),
        Job(id="job-1", name="index_photos", status="FAILED", task_count=3, completed_count=1,
            error_count=2, error="Could not scan the filesystem"),
    ])
    session.flush()
    session.add_all([PersonFace(person_id=1, face_id=11), PersonFace(person_id=1, face_id=10)])
    session.commit()
    store = Store(str(tmp_path / "queue.db"))
    fs = AssistantFS({"m1": media}, data_dir=data, timeout=2.0)
    monkeypatch.setattr(diag, "is_exiftool_available", lambda: True)
    monkeypatch.setattr(diag, "is_ffmpeg_available", lambda: True)
    provider = DiagnosticsToolProvider(
        session, groups=ALL, redactor=Redactor(home=tmp_path / "home"), fs=fs, store=store,
        model_label="Anthropic · claude-haiku", db_path=tmp_path / "lib.db")
    yield provider, session, store, data, media
    session.close()
    engine.dispose()


def _call(provider, tool, **args):
    result = provider.call_tool(tool, args)
    return result.model_text, result.host_data


def test_tools_follow_the_enabled_groups(env):
    provider = env[0]
    assert tool_names(frozenset()) == []
    names = tool_names(frozenset({"logs"}))
    assert "read_log" in names and "health_report" in names and "list_dir" not in names
    assert {t.name for t in provider.get_tools()} == set(tool_names(ALL))
    provider.groups = frozenset({"logs"})
    with pytest.raises(ValueError, match="disabled"):
        provider.call_tool("list_dir", {"media_dir_id": "m1"})


def test_result_is_wrapped_redacted_and_mirrored_to_the_activity(env):
    provider = env[0]
    text, activity = _call(provider, "settings_summary")
    assert text.startswith('<data source="settings_summary">')
    assert "~/Pictures" in text and "/home/Pictures" not in text
    assert activity["tool"] == "settings_summary"
    assert activity["detail"] in text


def test_library_stats_and_outliers(env):
    provider = env[0]
    text, activity = _call(provider, "library_stats")
    assert "Media items: 3" in text and "Impossible dates: 1" in text and "Undated: 1" in text
    text, activity = _call(provider, "date_outliers")
    assert "gone.jpg" in text and activity["count"] == 1


def test_face_consistency(env):
    text, activity = _call(env[0], "face_consistency")
    assert "Linked to a person but not ASSIGNED: 2" in text
    assert "PROCESSING: 1 with 0 face task(s)" in text
    assert "IGNORED but still linked: 1" in text


def test_media_item_report(env):
    provider = env[0]
    text, _ = _call(provider, "media_item_report", media_item_id=1)
    assert "media folder m1, 2019/a.jpg" in text and "File exists: yes" in text
    assert "person 1 (Alice Smith)" in text and "Tags: trip" in text
    missing, _ = _call(provider, "media_item_report", media_item_id=2)
    assert "File exists: NO" in missing
    unknown, activity = _call(provider, "media_item_report", media_item_id=99)
    assert activity["error"] is True


def test_files_tools(env):
    provider = env[0]
    text, _ = _call(provider, "media_dir_status")
    assert "id m1:" in text and "exists" in text
    text, _ = _call(provider, "list_dir", media_dir_id="m1", relative_path="2019")
    assert "a.jpg" in text
    text, _ = _call(provider, "stat_path", media_dir_id="m1", relative_path="2019/a.jpg")
    assert "file, 3 bytes" in text
    text, activity = _call(provider, "stat_path", media_dir_id="m1", relative_path="../../etc")
    assert "not allowed" in text and activity["error"] is True
    text, _ = _call(provider, "probe_media_dir", media_dir_id="m1")
    assert "responded in" in text


def test_logs_grouped_by_message(env):
    provider, data = env[0], env[3]
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        f"{now},123 - yaffo.x - index.py:10 - ERROR - Error processing photo /x/IMG_0001.jpg",
        f"{now},124 - yaffo.x - index.py:10 - ERROR - Error processing photo /x/IMG_0002.jpg",
        f"{now},125 - yaffo.x - index.py:12 - WARNING - Slow drive",
        f"{now},126 - yaffo.x - index.py:12 - INFO - fine",
        "2001-01-01 00:00:00,000 - yaffo.x - a.py:1 - ERROR - ancient",
    ]
    (data / "background_tasks.log").write_text("\n".join(lines) + "\n")
    text, activity = _call(provider, "recent_errors")
    assert "ERROR ×2 in background_tasks.log" in text and "WARNING ×1" in text
    assert "ancient" not in text and activity["count"] == 2
    text, _ = _call(provider, "read_log", name="background_tasks.log", tail=2)
    assert "INFO - fine" in text and "Slow drive" not in text


def test_jobs_and_queue(env):
    provider, _, store = env[0], env[1], env[2]
    text, _ = _call(provider, "worker_status")
    assert "never reported in" in text
    store.write_heartbeat(pid=42, started_at=time.time() - 30, workers=4, busy=1)
    task_id = store.insert_task("index_file", ["job-1", [1, 2, 3]], {})
    store.mark_running(task_id)
    store.mark_error(task_id, "OSError: [Errno 5] Input/output error\nTraceback...")
    text, _ = _call(provider, "worker_status")
    assert "Task host: running" in text and "workers alive 4, busy 1" in text

    text, _ = _call(provider, "recent_jobs", status="FAILED")
    assert "job-1 index_photos: FAILED" in text
    text, _ = _call(provider, "job_detail", job_id="job-1")
    assert "index_file(3 item(s)): error" in text and "Input/output error" in text
    text, activity = _call(provider, "failed_tasks")
    assert "index_file ×1" in text and activity["count"] == 1


def test_health_report(env):
    provider = env[0]
    text, activity = _call(provider, "health_report")
    assert text.splitlines()[1] == "Overall: problem"
    assert "linked to a person but not marked assigned" in text
    assert "never reported in" in text
    assert "impossible date" in text
    assert activity["count"] >= 3


def test_db_quick_check(env):
    text, _ = _call(env[0], "db_quick_check")
    assert "Quick check: ok" in text


def test_install_info_never_mentions_keys(env, monkeypatch):
    # A fake key, assembled so the secret-scanning pre-commit hook doesn't flag it.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "-".join(["sk", "ant", "secret", "value", "1234567890"]))
    text, _ = _call(env[0], "install_info")
    assert "Version:" in text and "Anthropic · claude-haiku" in text
    assert "sk-ant" not in text


def test_watcher_and_web_status_are_reported(env, monkeypatch):
    provider = env[0]
    monkeypatch.setattr(provider.fs, "process_status", lambda role: {
        "pid": 1, "started_at": 1, "beat_at": 1, "healthy": False})
    assert "File watcher: NOT RESPONDING" in provider.call_tool("worker_status", {}).model_text
    report = provider.call_tool("install_info", {}).model_text
    assert "Web server started" in report and "NOT RESPONDING" in report
    assert any(f.check == "watcher" and f.level == "warning" for f in provider.health_findings())


def test_media_report_reads_metadata_only_when_enabled(env, monkeypatch):
    provider = env[0]
    calls = []
    monkeypatch.setattr(provider.fs, "capture_date_source", lambda *args: calls.append(args) or {"source": "none"})
    provider.call_tool("media_item_report", {"media_item_id": 1})
    assert calls == []
    provider.groups |= {"metadata"}
    result = provider.call_tool("media_item_report", {"media_item_id": 1})
    assert calls == [("m1", "2019/a.jpg")]
    assert "not proof of the original indexing source" in result.model_text


# ---- phase 5: sharing and automation settings ---------------------------------------------

def test_sharing_status_reads_devices_and_the_web_heartbeats_transfers(env):
    provider, session, _, data, _ = env
    session.add_all([KnownDevice(device_id="d1", pubkey="k", display_name="Laptop", trust_state="TRUSTED"),
                     ShareGrant(peer_device_id="d1", scope_type="album", album_id=None)])
    session.commit()

    text, _ = _call(provider, "sharing_status")
    assert "Engine: unknown" in text and "- Laptop: trusted" in text and "album ×1" in text

    write_status("web", time.time(), data_dir=data, extra={"sharing": {
        "available": True, "hub_connected": False,
        "transfers": [{"peer_name": "Laptop", "label": "Trip", "state": "failed", "files_done": 2,
                       "files_total": 5, "files_failed": 3, "error": "Peer went away",
                       "failed_files": ["timeout"]}]}})
    text, _ = _call(provider, "sharing_status")
    assert "hub NOT connected" in text
    assert "from Laptop: Trip — failed, 2/5 files, 3 failed; error: Peer went away; file errors: timeout" in text


def test_web_heartbeat_publishes_registered_extras_and_skips_failing_ones(tmp_path, monkeypatch):
    monkeypatch.setattr(process_status, "_WEB_EXTRAS", {})
    process_status.register_web_extra("sharing", lambda: {"hub_connected": True})
    process_status.register_web_extra("broken", lambda: 1 / 0)
    write_status("web", time.time(), data_dir=tmp_path, extra=process_status._web_extras())
    assert read_status("web", data_dir=tmp_path)["extra"] == {"sharing": {"hub_connected": True}}


def test_automation_config_shows_values_defaults_and_meaning(env):
    provider, session, _, _, _ = env
    session.add_all([Automation(slug="names", name="Names", handler="assign_location_name",
                                config={"nearby_radius": 3, "nearby_radius_unit": "km"}),
                     Automation(slug="plain", name="Plain")])
    session.commit()

    text, _ = _call(provider, "automation_config", slug="names")
    assert "Nearby radius (nearby_radius): 3 km (default" in text and "How close an already-named photo" in text
    assert "has no settings" in _call(provider, "automation_config", slug="plain")[0]
    assert _call(provider, "automation_config", slug="nope")[1]["error"] is True
