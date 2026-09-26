"""The assistant adds no network access of its own (ai-assistant.md → Network access).

1. No module in site_agents/assistant imports an HTTP or socket library.
2. The docs tools, every diagnostic tool, and run_script (with every read host
   function of the assistant profile) work with socket connections blocked.
3. Approving and undoing change plans with every library change opens no
   connection either, and no host function the assistant gets can reach reverse
   geocoding (the one network path among library features).
"""
import ast
import inspect
import json
import socket
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from yaffo.background_tasks.automation_sandbox import automation_actions, maintenance_actions
from yaffo.background_tasks.automation_sandbox.automation_host import host_api
from yaffo import themes
from yaffo.db import db
from yaffo.db.models import (
    FACE_STATUS_PROCESSING, FACE_STATUS_UNASSIGNED, JOB_STATUS_COMPLETED, JOB_STATUS_RUNNING, PLAN_STATUS_UNDONE,
    ApplicationSettings,
    Automation, Face, Job, MediaItem, Person,
)
from yaffo.db.repositories import assistant_repository
from yaffo.site_agents import assistant
from yaffo.site_agents.assistant import plans
from yaffo.site_agents.assistant.tool_providers.diagnostics import diagnostics as diag
from yaffo.site_agents.assistant.tool_providers.diagnostics.diagnostics import TOOLS, DiagnosticsToolProvider
from yaffo.site_agents.assistant.tool_providers.diagnostics.fs import AssistantFS
from yaffo.site_agents.assistant.tool_providers.knowledge.knowledge import DocSection, KnowledgeBase
from yaffo.site_agents.assistant.tool_providers.links import LINK_TO_PAGE, LINK_TO_PHOTOS, LinkToolProvider
from yaffo.site_agents.assistant.tool_providers.script_tool import RUN_SCRIPT, ScriptToolProvider
from yaffo.site_agents.assistant.tool_providers.knowledge.tools import READ_DOC, SEARCH_DOCS, KnowledgeToolProvider
from yaffo.taskq.store import Store

pytestmark = pytest.mark.unit

NETWORK_MODULES = {"requests", "httpx", "urllib.request", "urllib3", "socket", "aiohttp", "http.client",
                   "websockets", "aioquic"}

SAMPLE_ARGS = {
    "media_item_report": {"media_item_id": 1},
    "capture_date_source": {"media_item_id": 1},
    "read_log": {"name": "yaffo.log"},
    "probe_media_dir": {"media_dir_id": "m1"},
    "stat_path": {"media_dir_id": "m1", "relative_path": ""},
    "list_dir": {"media_dir_id": "m1"},
    "job_detail": {"job_id": "none"},
    "automation_runs": {"slug": "none"},
    "automation_config": {"slug": "none"},
}

READ_SCRIPTS = {
    "data_query": 'data_query({"source": "media_items", "limit": 1})',
    "match_people": "match_people(1)",
    "face_similarity": "face_similarity(1, 1)",
    "suggest_face_clusters": "suggest_face_clusters()",
    "find_similar_faces": "find_similar_faces(1)",
    "duplicate_groups": 'duplicate_groups("scan")',
    "suggest_location_names": "suggest_location_names([1])",
}


def _imports(path: Path) -> set[str]:
    names = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_assistant_modules_import_no_network_library():
    package_dir = Path(assistant.__file__).parent
    offenders = {
        str(path.relative_to(package_dir)): sorted(name for name in _imports(path) if name.split(".")[0] in NETWORK_MODULES or name in NETWORK_MODULES)
        for path in package_dir.rglob("*.py")
    }
    assert {name: found for name, found in offenders.items() if found} == {}


@pytest.fixture
def offline(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("the assistant tried to open a network connection")
    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)


@pytest.fixture
def session(tmp_path):
    media = tmp_path / "media"
    media.mkdir()
    engine = create_engine(f"sqlite:///{tmp_path / 'lib.db'}")
    db.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(ApplicationSettings(name="media_dirs", type="json",
                                        value=json.dumps([{"id": "m1", "path": str(media)}])))
        session.add_all([MediaItem(id=1, full_file_path=str(media / "a.jpg"), media_type="photo"),
                         Person(id=1, name="Alice")])
        session.commit()
        yield session, tmp_path
    engine.dispose()


def test_every_tool_runs_offline(session, offline, monkeypatch):
    session, tmp_path = session
    session.add(Job(id="scan", name="find_duplicates", status=JOB_STATUS_COMPLETED))
    session.commit()
    monkeypatch.setattr(diag, "is_exiftool_available", lambda: False)
    monkeypatch.setattr(diag, "is_ffmpeg_available", lambda: False)

    knowledge = KnowledgeToolProvider(KnowledgeBase([DocSection(
        id="guide/a.md#", scope="guide", path="guide/a.md", page_title="A", heading="A", level=1,
        anchor="", url="https://docs/a/", text="Adding folders.")]))
    knowledge.call_tool(SEARCH_DOCS, {"query": "folders"})
    knowledge.call_tool(READ_DOC, {"path": "guide/a.md"})

    data = tmp_path / "data"
    data.mkdir()
    diagnostics = DiagnosticsToolProvider(
        session, groups=frozenset({"logs", "library", "files", "jobs", "metadata"}),
        fs=AssistantFS({"m1": tmp_path / "media"}, data_dir=data),
        store=Store(str(tmp_path / "queue.db")), db_path=tmp_path / "lib.db")
    for tool in TOOLS:
        diagnostics.call_tool(tool.name, SAMPLE_ARGS.get(tool.name, {}))

    links = LinkToolProvider(session)
    assert links.call_tool(LINK_TO_PHOTOS, {"title": "All", "filters": {}}).host_data["links"]
    assert links.call_tool(LINK_TO_PAGE, {"title": "One", "page": "media_view", "values": {"media_item_id": 1}}).host_data["links"]

    scripts = ScriptToolProvider(session)
    read_functions = {fn.name for fn in host_api("assistant") if not fn.mutating}
    assert read_functions == set(READ_SCRIPTS)
    for name, code in READ_SCRIPTS.items():
        result = scripts.call_tool(RUN_SCRIPT, {"code": code, "purpose": name})
        assert result.host_data["error"] is False, result.model_text


def test_no_assistant_host_function_can_reach_reverse_geocode():
    """Reverse geocoding (OpenStreetMap) is the network path among library
    features; nothing the assistant can call may reach it. Checked at the module
    level, so a helper next to an impl counts too."""
    for fn in host_api("assistant"):
        module = inspect.getmodule(fn.impl)
        assert "reverse_geocode" not in inspect.getsource(module), (fn.name, module.__name__)


# One call to every offline, non-file library change in the assistant profile.
OFFLINE_CHANGES = """
album = create_album("Trip")
add_to_album(album, [1])
set_album_cover(album, 1)
reorder_album(album, [1])
update_album(album, "Trip 2")
remove_from_album(album, [1])
tag_media_items([{"media_item_id": 1, "name": "beach"}])
untag_media_items([{"media_item_id": 1, "name": "beach"}])
assign_faces([{"face_id": 1, "person_id": 1}])
unassign_faces([{"face_id": 1}])
ignore_faces([1])
unignore_faces([1])
set_favorites([{"id": 1, "favorite": True}])
set_media_dates([{"id": 1, "date": "2020-01-02T03:04:05"}])
set_location_names([{"id": 1, "location_name": "Coast"}])
person = create_person("Bea")
rename_person(person, "Bea Smith")
set_person_birthdate(person, "2015-06-01")
set_coordinates([{"id": 1, "latitude": 48.85, "longitude": 2.29}])
add_label_to_vocabulary("sailboat")
set_default_theme("darkroom")
set_locale("de")
set_distance_unit("km")
set_filter_layout([{"key": "people", "visible": True}])
set_automation_enabled("offline-test", True)
"""


def test_approving_and_undoing_a_plan_runs_offline(session, offline, monkeypatch):
    session, _ = session
    monkeypatch.setattr(automation_actions, "emit_event", lambda *args: None)
    monkeypatch.setattr(maintenance_actions, "face_tasks_active", lambda: False)
    monkeypatch.setattr(themes, "_cached_theme", None)
    session.add_all([Face(id=1, media_item_id=1, status=FACE_STATUS_UNASSIGNED),
                     Face(id=2, media_item_id=1, status=FACE_STATUS_PROCESSING),
                     Automation(slug="offline-test", name="Offline test", enabled=False),
                     Job(id="offline-job", name="find_duplicates", status=JOB_STATUS_RUNNING)])
    session.commit()
    # Background work (retry, reindex, scan, sync) only queues a task here; the
    # tasks do local file work when they run.
    offline_changes = {fn.name for fn in host_api("assistant")
                       if fn.mutating and fn.risk != "high" and not fn.starts_job}
    reversible = {fn.name for fn in host_api("assistant") if fn.name in offline_changes and fn.undo}
    conversation = assistant_repository.create_conversation(session, "Offline")
    scripts = ScriptToolProvider(session, conversation_id=conversation.id, actions=frozenset(offline_changes))

    result = scripts.call_tool(RUN_SCRIPT, {"code": OFFLINE_CHANGES, "purpose": "Every change"})
    plan = assistant_repository.get_plan(session, result.host_data["plan_id"])
    assert {step.name for step in plans.load_steps(plan)} == reversible

    plans.approve(session, plan.id, conversation.id)
    assert [step.state for step in plans.load_steps(plan)] == ["done"] * len(plans.load_steps(plan))
    plans.undo(session, plan.id, conversation.id)
    assert plan.status == PLAN_STATUS_UNDONE and plan.error is None

    # The offline changes without an undo, approved on their own.
    code = ('delete_album(create_album("Gone"))\nrepair_face_statuses()\ncancel_job("offline-job")\n'
            'delete_label(add_label_to_vocabulary("gone"))')
    assert {"delete_album", "repair_face_statuses", "cancel_job", "delete_label"} | reversible == offline_changes
    result = scripts.call_tool(RUN_SCRIPT, {"code": code, "purpose": "Delete"})
    plans.approve(session, result.host_data["plan_id"], conversation.id)


def test_merging_and_deleting_people_runs_offline(session, offline, monkeypatch):
    session, _ = session
    monkeypatch.setattr(automation_actions, "emit_event", lambda *args: None)
    session.add_all([Person(id=2, name="Al"), Person(id=3, name="Gone"),
                     ApplicationSettings(name="assistant_action_merge_people", type="string", value="true"),
                     ApplicationSettings(name="assistant_action_delete_person", type="string", value="true")])
    session.commit()
    conversation = assistant_repository.create_conversation(session, "People")
    scripts = ScriptToolProvider(session, conversation_id=conversation.id,
                                 actions=frozenset({"merge_people", "delete_person"}))

    result = scripts.call_tool(RUN_SCRIPT, {"code": "merge_people(2, 1)\ndelete_person(3)", "purpose": "People"})
    plans.approve(session, result.host_data["plan_id"], conversation.id, confirm_count=1)

    assert {p.id for p in session.query(Person)} == {1}
