"""The assistant adds no network access of its own (ai-assistant.md → Network access).

1. No module in site_agents/assistant imports an HTTP or socket library.
2. The docs tools, every diagnostic tool, and run_script (with every read host
   function of the assistant profile) work with socket connections blocked.
"""
import ast
import inspect
import json
import socket
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from yaffo.background_tasks.automation_sandbox.automation_host import host_api
from yaffo.db import db
from yaffo.db.models import ApplicationSettings, MediaItem, Person
from yaffo.site_agents.assistant import diagnostics as diag
from yaffo.site_agents.assistant.diagnostics import TOOLS, DiagnosticsToolProvider
from yaffo.site_agents.assistant.fs import AssistantFS
from yaffo.site_agents.assistant.knowledge import DocSection, KnowledgeBase
from yaffo.site_agents.assistant.links import LINK_TO_MEDIA_ITEM, LINK_TO_PHOTOS, LinkToolProvider
from yaffo.site_agents.assistant.script_tool import RUN_SCRIPT, ScriptToolProvider
from yaffo.site_agents.assistant.tools import READ_DOC, SEARCH_DOCS, KnowledgeToolProvider
from yaffo.taskq.store import Store

pytestmark = pytest.mark.unit

NETWORK_MODULES = {"requests", "httpx", "urllib.request", "urllib3", "socket", "aiohttp", "http.client",
                   "websockets", "aioquic"}

SAMPLE_ARGS = {
    "media_item_report": {"media_item_id": 1},
    "read_log": {"name": "yaffo.log"},
    "probe_media_dir": {"media_dir_id": "m1"},
    "stat_path": {"media_dir_id": "m1", "relative_path": ""},
    "list_dir": {"media_dir_id": "m1"},
    "job_detail": {"job_id": "none"},
    "automation_runs": {"slug": "none"},
}

READ_SCRIPTS = {
    "data_query": 'data_query({"source": "media_items", "limit": 1})',
    "match_people": "match_people(1)",
    "face_similarity": "face_similarity(1, 1)",
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
    package_dir = Path(diag.__file__).parent
    offenders = {
        path.name: sorted(name for name in _imports(path) if name.split(".")[0] in NETWORK_MODULES or name in NETWORK_MODULES)
        for path in package_dir.glob("*.py")
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
        session, groups=frozenset({"logs", "library", "files", "jobs"}),
        fs=AssistantFS({"m1": tmp_path / "media"}, data_dir=data),
        store=Store(str(tmp_path / "queue.db")), db_path=tmp_path / "lib.db")
    for tool in TOOLS:
        diagnostics.call_tool(tool.name, SAMPLE_ARGS.get(tool.name, {}))

    links = LinkToolProvider(session)
    assert links.call_tool(LINK_TO_PHOTOS, {"title": "All", "filters": {}}).host_data["links"]
    assert links.call_tool(LINK_TO_MEDIA_ITEM, {"title": "One", "media_item_id": 1}).host_data["links"]

    scripts = ScriptToolProvider(session)
    read_functions = {fn.name for fn in host_api("assistant") if not fn.mutating}
    assert read_functions == set(READ_SCRIPTS)
    for name, code in READ_SCRIPTS.items():
        result = scripts.call_tool(RUN_SCRIPT, {"code": code, "purpose": name})
        assert result.host_data["error"] is False, result.model_text


def test_host_functions_calling_reverse_geocode_declare_uses_network():
    """Reverse geocoding (OpenStreetMap) is the only network path among library
    edits; a host function that calls it must say so."""
    for fn in host_api("assistant"):
        if "reverse_geocode" in inspect.getsource(fn.impl):
            assert fn.uses_network, fn.name
