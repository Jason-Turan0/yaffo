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


def test_host_returns_are_coerced_to_sandbox_safe_types(monkeypatch):
    # DB rows can carry dates (people.birthdate) / Decimals, which the starlark-pyo3
    # binding can't marshal back; the host boundary coerces them to ISO strings / floats.
    import datetime
    from decimal import Decimal

    monkeypatch.setattr(
        "yaffo.background_tasks.automation_sandbox.automation_actions.resolve_query",
        lambda session, query: [{"name": "Chase", "birthdate": datetime.date(2015, 6, 1), "score": Decimal("0.5")}],
    )
    monkeypatch.setattr(
        "yaffo.background_tasks.automation_sandbox.automation_actions.enrich_media_rows",
        lambda session, rows: rows,
    )

    result = run_starlark(
        "data_query({'source': 'people'})",
        functions=build_host_functions(object()),
    )

    assert result.success is True, result.error
    assert result.value == [{"name": "Chase", "birthdate": "2015-06-01", "score": 0.5}]


def test_data_query_callable_is_invoked_from_starlark(monkeypatch):
    calls = []

    def fake_resolve_query(session, query):
        calls.append((session, query))
        return [{"id": 1}, {"id": 2}]

    monkeypatch.setattr(
        "yaffo.background_tasks.automation_sandbox.automation_actions.resolve_query", fake_resolve_query
    )
    monkeypatch.setattr(
        "yaffo.background_tasks.automation_sandbox.automation_actions.enrich_media_rows",
        lambda session, rows: rows,  # enrichment tested separately
    )

    session = object()  # sentinel; the fake doesn't touch it
    functions = build_host_functions(session)
    assert "data_query" in functions

    result = run_starlark(
        "data_query({'source': 'media_items', 'limit': 5})", functions=functions
    )

    assert result.success is True, result.error
    # the host callable ran exactly once, with the script's query dict + the bound session
    assert len(calls) == 1
    called_session, called_query = calls[0]
    assert called_session is session
    assert called_query == {"source": "media_items", "limit": 5}
    # and its return value flows back as the script's value
    assert result.value == [{"id": 1}, {"id": 2}]


def test_data_query_not_called_when_script_omits_it(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "yaffo.background_tasks.automation_sandbox.automation_actions.resolve_query",
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


def test_report_progress_is_bound_to_the_injected_reporter():
    # report_progress (injects="progress") gets the run's reporter, not the session;
    # the others get the session. No global state, no contextvar.
    class _Reporter:
        def __init__(self):
            self.updates = []

        def progress_update(self, task_count, completed, cancelled, errors):
            self.updates.append((task_count, completed, cancelled, errors))

    reporter = _Reporter()
    functions = build_host_functions(object(), reporter)

    result = run_starlark("report_progress(2, 8)", functions=functions)

    assert result.success is True, result.error
    assert reporter.updates == [(8, 2, 0, 0)]


def test_report_progress_noops_without_a_reporter_in_preview():
    # A preview builds host functions with no reporter (None) -> the call is a no-op
    # and is recorded like any other action.
    functions, calls = build_recording_host_functions(object())  # progress defaults to None
    result = run_starlark("report_progress(2, 8)", functions=functions)
    assert result.success is True, result.error
    assert calls == [HostCall(name="report_progress", args=[2, 8])]


def test_recording_run_skips_mutating_impl_but_records_it(monkeypatch):
    performed = []
    monkeypatch.setattr(
        "yaffo.background_tasks.automation_sandbox.automation_actions.media_repository.add_tags",
        lambda *a, **k: performed.append(a),
    )
    functions, calls = build_recording_host_functions(object())

    result = run_starlark("tag_media_items([{'media_item_id': 1, 'name': 'beach'}])", functions=functions)

    assert result.success is True, result.error
    assert performed == []                       # mutating impl not executed
    assert calls == [HostCall(name="tag_media_items", args=[[{"media_item_id": 1, "name": "beach"}]])]  # but recorded


def test_live_run_performs_mutating_impl(monkeypatch):
    performed = []
    monkeypatch.setattr(
        "yaffo.background_tasks.automation_sandbox.automation_actions.media_repository.add_tags",
        lambda session, items: performed.extend(items),
    )
    run_starlark("tag_media_items([{'media_item_id': 1, 'name': 'beach'}])", functions=build_host_functions(object()))
    assert performed == [(1, "beach", None)]


def test_summaries_are_friendly(monkeypatch):
    # read summaries resolve ids to file names / person names via labels
    monkeypatch.setattr(
        "yaffo.background_tasks.automation_sandbox.labels.media_repository.get_media_item_filename",
        lambda session, media_item_id: {5: "p5.jpg"}.get(media_item_id),
    )

    class _P:
        def __init__(self, name): self.name = name

    monkeypatch.setattr(
        "yaffo.background_tasks.automation_sandbox.labels.person_repository.get_person_by_id",
        lambda session, person_id: _P("Grandma") if person_id == 9 else None,
    )
    s = object()
    assert summarize_call(HostCall("data_query", [{"source": "media_items"}]), s) == "Looking up media_items"
    assert summarize_call(HostCall("report_progress", [3, 10]), s) == "Report progress: 3/10"
    assert summarize_call(HostCall("face_similarity", [5, 9]), s) == "Compare faces in p5.jpg to Grandma"
    assert summarize_call(HostCall("match_people", [5]), s) == "Match faces in p5.jpg to known people"
    # batch writes summarize by count
    assert summarize_call(HostCall("tag_media_items", [[{"media_item_id": 1, "name": "a"}, {"media_item_id": 2, "name": "b"}]]), s) == "Tag 2 photo(s)"
    assert summarize_call(HostCall("assign_faces", [[{"face_id": 1, "person_id": 2}]]), s) == "Assign 1 face(s)"
    assert summarize_call(HostCall("move_media_items", [[{"media_item_id": 1}]]), s) == "Move 1 photo(s)"
    assert summarize_call(HostCall("rename_files", [[]]), s) == "Rename 0 file(s)"
    assert summarize_call(HostCall("delete_media_items", [[1, 2, 3]]), s) == "Delete 3 photo(s)"


def test_batch_write_is_recorded_not_performed(monkeypatch):
    """A batch mutating call is recorded for the preview but its impl doesn't run."""
    performed = []
    monkeypatch.setattr(
        "yaffo.background_tasks.automation_sandbox.automation_actions.media_repository.add_tags",
        lambda *a, **k: performed.append(a),
    )
    functions, calls = build_recording_host_functions(object())

    result = run_starlark("tag_media_items([{'media_item_id': 1, 'name': 'beach'}])", functions=functions)

    assert result.success is True, result.error
    assert performed == []
    assert calls == [HostCall(name="tag_media_items", args=[[{"media_item_id": 1, "name": "beach"}]])]


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
