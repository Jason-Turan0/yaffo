"""Unit tests for the sandboxed automation host API (background_tasks/automation_host).

Verifies the curated host callables are reachable from Starlark and delegate to
the real implementation -- here, that `data_query(...)` invoked inside a script
calls resolve_query with the converted query dict and feeds its result back.
"""
import pytest

from yaffo.background_tasks.automation_sandbox.automation_host import (
    HOST_API,
    HostCall,
    build_host_functions,
    build_recording_host_functions,
    render_host_api,
    summarize_call,
)
from yaffo.background_tasks.automation_sandbox.starlark_runner import run_starlark

pytestmark = pytest.mark.unit


def test_data_query_callable_is_invoked_from_starlark(monkeypatch):
    calls = []

    def fake_resolve_query(session, query):
        calls.append((session, query))
        return [{"id": 1}, {"id": 2}]

    monkeypatch.setattr(
        "yaffo.background_tasks.automation_sandbox.automation_host.resolve_query", fake_resolve_query
    )

    session = object()  # sentinel; the fake doesn't touch it
    functions = build_host_functions(session)
    assert "data_query" in functions

    result = run_starlark(
        "data_query({'source': 'photos', 'limit': 5})", functions=functions
    )

    assert result.success is True, result.error
    # the host callable ran exactly once, with the script's query dict + the bound session
    assert len(calls) == 1
    called_session, called_query = calls[0]
    assert called_session is session
    assert called_query == {"source": "photos", "limit": 5}
    # and its return value flows back as the script's value
    assert result.value == [{"id": 1}, {"id": 2}]


def test_data_query_not_called_when_script_omits_it(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "yaffo.background_tasks.automation_sandbox.automation_host.resolve_query",
        lambda session, query: calls.append(query),
    )

    result = run_starlark("1 + 1", functions=build_host_functions(object()))

    assert result.success is True
    assert result.value == 2
    assert calls == []


def test_runtime_surface_matches_declared_api():
    # build_host_functions and the docs both derive from HOST_API, so the live
    # callables are exactly the advertised ones -- no drift between them.
    declared = {fn.name for fn in HOST_API}
    assert set(build_host_functions(object()).keys()) == declared


def test_rendered_docs_cover_every_host_function():
    docs = render_host_api()
    for fn in HOST_API:
        assert fn.signature in docs
        assert fn.example in docs
        assert fn.returns in docs


def test_recording_run_skips_mutating_impl_but_records_it(monkeypatch):
    performed = []
    monkeypatch.setattr(
        "yaffo.background_tasks.automation_sandbox.automation_actions.photos_repository.add_tag",
        lambda *a, **k: performed.append(a),
    )
    functions, calls = build_recording_host_functions(object())

    result = run_starlark("tag_photo(1, 'beach')", functions=functions)

    assert result.success is True, result.error
    assert performed == []                       # mutating impl not executed
    assert calls == [HostCall(name="tag_photo", args=[1, "beach"])]  # but recorded


def test_live_run_performs_mutating_impl(monkeypatch):
    performed = []
    monkeypatch.setattr(
        "yaffo.background_tasks.automation_sandbox.automation_actions.photos_repository.add_tag",
        lambda session, photo_id, name, value=None: performed.append((photo_id, name)),
    )
    run_starlark("tag_photo(1, 'beach')", functions=build_host_functions(object()))
    assert performed == [(1, "beach")]


def test_summaries_are_friendly(monkeypatch):
    # summaries resolve ids to file names / person names via labels
    monkeypatch.setattr(
        "yaffo.background_tasks.automation_sandbox.labels.photos_repository.get_photo_filename",
        lambda session, photo_id: {12: "beach.jpg", 3: "old.jpg", 5: "p5.jpg"}.get(photo_id),
    )
    monkeypatch.setattr(
        "yaffo.background_tasks.automation_sandbox.labels.photos_repository.get_photo_filename_for_face",
        lambda session, face_id: "fam.jpg" if face_id == 8 else None,
    )

    class _P:
        def __init__(self, name): self.name = name

    monkeypatch.setattr(
        "yaffo.background_tasks.automation_sandbox.labels.person_repository.get_person_by_id",
        lambda session, person_id: _P("Grandma") if person_id == 9 else None,
    )
    s = object()
    assert summarize_call(HostCall("tag_photo", [12, "beach"]), s) == "Tag beach.jpg as 'beach'"
    assert summarize_call(HostCall("rename_file", [3, "x.jpg"]), s) == "Rename old.jpg to 'x.jpg'"
    assert summarize_call(HostCall("assign_face", [8, 9]), s) == "Assign Grandma to a face in fam.jpg"
    assert summarize_call(HostCall("data_query", [{"source": "photos"}]), s) == "Looking up photos"
    assert summarize_call(HostCall("face_similarity", [5, 9]), s) == "Compare faces in p5.jpg to Grandma"
    assert summarize_call(HostCall("match_people", [5]), s) == "Match faces in p5.jpg to known people"


def test_face_similarity_is_read_only_and_runs_in_preview(monkeypatch):
    # read-only compare functions execute (and are recorded) even in a recording run
    monkeypatch.setattr(
        "yaffo.background_tasks.automation_sandbox.automation_compare.person_repository.get_person_by_id",
        lambda session, person_id: None,  # unknown person -> empty scores, no embedding math
    )
    functions, calls = build_recording_host_functions(object())
    result = run_starlark("face_similarity(1, 9)", functions=functions)
    assert result.success is True, result.error
    assert result.value == []
    assert calls == [HostCall(name="face_similarity", args=[1, 9])]
