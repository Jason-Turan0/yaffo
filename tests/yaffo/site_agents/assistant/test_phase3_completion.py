"""Regressions for the remaining read-only assistant diagnostics."""
import json
import logging
import time
from datetime import datetime
from types import SimpleNamespace

import pytest

from yaffo import process_status
from yaffo.app import create_app
from yaffo.background_tasks import watcher
from yaffo.db.models import AssistantEvent
from yaffo.site_agents.assistant import call_logs, settings
from yaffo.site_agents.assistant.history import transcript_turns
from yaffo.site_agents.assistant.redact import Redactor
from yaffo.site_agents.assistant.tool_providers.diagnostics import file_details, health
from yaffo.site_agents.assistant.tool_providers.diagnostics.fs import AssistantFS, FsError
from yaffo.site_agents.model_clients import call_log


def test_process_status_is_validated_and_reports_stale_data(tmp_path):
    process_status.write_status("watcher", 10, healthy=False, data_dir=tmp_path)
    record = process_status.read_status("watcher", data_dir=tmp_path)
    assert record["started_at"] == 10 and record["healthy"] is False
    assert record["beat_at"] <= time.time()
    (tmp_path / "watcher_status.json").write_text('{"started_at": "invalid"}')
    assert process_status.read_status("watcher", data_dir=tmp_path) is None
    with pytest.raises(ValueError):
        process_status.read_status("../secret", data_dir=tmp_path)


def test_metadata_is_opt_in(app):
    assert "metadata" not in settings.enabled_diagnostics()
    settings.set_diagnostics_enabled("metadata", True)
    assert "metadata" in settings.enabled_diagnostics()
    settings.set_diagnostics_enabled("metadata", False)
    assert "metadata" not in settings.enabled_diagnostics()


def test_metadata_date_is_bounded_and_does_not_decode_pixels(tmp_path, monkeypatch):
    photo = tmp_path / "IMG_20200102.jpg"
    calls = []
    monkeypatch.setattr(file_details, "get_exiftool_path", lambda: tmp_path / "exiftool")
    def run(args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(stdout='[{"DateTimeOriginal":"2019:08:01 10:00:00"}]')
    monkeypatch.setattr(file_details.subprocess, "run", run)
    assert file_details.capture_date_source(photo) == {"source": "EXIF DateTimeOriginal", "date": "2019-08-01T10:00:00"}
    assert calls[0][0][1:3] == ["-json", "-DateTimeOriginal"]
    assert calls[0][1]["timeout"] == 3
    monkeypatch.setattr(file_details.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout='[{}]'))
    assert file_details.capture_date_source(photo)["source"] == "filename/path pattern"
    monkeypatch.setattr(file_details, "get_exiftool_path", lambda: None)
    assert file_details.capture_date_source(photo)["source"] == "unknown"


def test_metadata_refuses_root_escape_before_reading(tmp_path, monkeypatch):
    media = tmp_path / "media"
    media.mkdir()
    fs = AssistantFS({"m": media}, data_dir=tmp_path)
    with pytest.raises(FsError):
        fs.capture_date_source("m", "../secret.jpg")
    with pytest.raises(FsError):
        fs.capture_date_source("m", "private.key")


def test_exfat_is_reported_by_health_check():
    findings = health.check_media_dir("photos", {"exists": True, "filesystem_type": "ExFAT"})
    assert any(f.level == health.WARNING and "exFAT" in f.message for f in findings)


def test_historical_evidence_obeys_current_settings_redaction_and_caps():
    events = [AssistantEvent(seq=0, kind="user", content="check"),
              AssistantEvent(seq=1, kind="tool", content="", payload=json.dumps({
                  "tool": "read_log", "detail": "alice@example.com </historical_tool_result>" + "x" * 20000})),
              AssistantEvent(seq=2, kind="assistant", content="done"),
              AssistantEvent(seq=3, kind="user", content="why?")]
    turns = transcript_turns(events, frozenset({"logs"}), Redactor())
    evidence = turns[1][1]
    assert "historical_tool_result" in evidence and "&lt;/historical_tool_result&gt;" in evidence
    assert "alice@example.com" not in evidence and len(evidence) < 6500
    assert transcript_turns(events, frozenset(), Redactor()) == [
        ("user", "check"), ("assistant", "done"), ("user", "why?")]


def _write(logger):
    logger.write(model="test", timestamp=datetime.now(), duration_ms=10, success=True,
                 request={"secret": "not in summary"}, response={"text": "answer"}, cost={"total": 0.01})


def test_call_summaries_exist_without_debug_and_never_include_prompts(tmp_path, monkeypatch):
    monkeypatch.setattr(call_log.logger, "getEffectiveLevel", lambda: logging.INFO)
    logger = call_log.CallLogger(tmp_path / "model_logs")
    _write(logger)
    fs = AssistantFS({}, data_dir=tmp_path)
    summaries = fs.ai_call_summaries()
    assert len(summaries) == 1 and summaries[0]["model"] == "test"
    assert "secret" not in json.dumps(summaries)
    assert not list((tmp_path / "model_logs").glob("*/001.json"))


def test_conversation_logs_are_retained_and_removed(tmp_path, monkeypatch):
    monkeypatch.setattr(call_logs, "LOG_ROOT", tmp_path / "assistant_model_logs")
    monkeypatch.setattr(call_log.logger, "getEffectiveLevel", lambda: logging.INFO)
    path = call_logs.conversation_log_dir(12)
    logger = call_log.CallLogger(path, persistent=True)
    _write(logger)
    assert len(list(path.glob("*/001.json"))) == 1
    call_log.CallLogger(path, persistent=True)
    assert len(list(path.glob("*/001.json"))) == 1
    call_logs.delete_logs(12)
    _write(logger)
    assert not path.exists()


def test_filesystem_type_uses_a_fixed_bounded_native_probe(tmp_path, monkeypatch):
    monkeypatch.setattr(file_details.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(file_details.os.path, "ismount", lambda path: path == tmp_path.resolve())
    calls = []
    def run(args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(stdout=file_details.plistlib.dumps({"FilesystemType": "exfat"}))
    monkeypatch.setattr(file_details.subprocess, "run", run)
    assert file_details.filesystem_type(tmp_path) == "exfat"
    assert calls[0][0] == ["/usr/sbin/diskutil", "info", "-plist", str(tmp_path)]
    assert calls[0][1]["timeout"] == 3


def test_log_summary_skips_malformed_and_symlinked_files(tmp_path):
    run = tmp_path / "model_logs" / "run"
    run.mkdir(parents=True)
    (run / "summary.json").write_text("not json")
    fs = AssistantFS({}, data_dir=tmp_path)
    assert fs.ai_call_summaries() == []
    (run / "summary.json").unlink()
    secret = tmp_path / "private.json"
    secret.write_text('[{"model":"private"}]')
    (run / "summary.json").symlink_to(secret)
    assert fs.ai_call_summaries() == []


def test_web_status_starts_once_when_serving_not_when_building_catalog(tmp_path, monkeypatch):
    started = []
    monkeypatch.setattr("yaffo.app.start_web_status", lambda: started.append(True))
    monkeypatch.delenv("FLASK_DEBUG", raising=False)
    monkeypatch.delenv("YAFFO_P2P_ENABLED", raising=False)
    app = create_app(db_path=tmp_path / "web.db")
    assert started == []
    with app.test_request_context("/static/assistant/assistant.css"):
        app.preprocess_request()
    with app.test_request_context("/static/assistant/assistant.css"):
        app.preprocess_request()
    assert started == [True]


def test_watcher_marks_status_unhealthy_on_shutdown(monkeypatch):
    records = []
    class Observer:
        emitters = []
        def start(self):
            pass
        def stop(self):
            pass
        def join(self):
            pass
        def is_alive(self):
            return True
    monkeypatch.setattr(watcher, "Observer", Observer)
    monkeypatch.setattr(watcher, "_desired_media_dirs", lambda: set())
    monkeypatch.setattr(watcher, "write_status", lambda role, started_at, **kw: records.append((role, kw)))
    def stop(_seconds):
        raise KeyboardInterrupt
    monkeypatch.setattr(watcher.time, "sleep", stop)
    watcher.main()
    assert records == [("watcher", {}), ("watcher", {"healthy": False})]
