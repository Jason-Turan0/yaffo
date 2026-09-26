"""Real evaluator and DB coverage for phase-two sandbox/preview boundaries."""
import json
import subprocess
import sys
import time

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from yaffo.background_tasks.automation_sandbox import automation_actions as actions
from yaffo.background_tasks.automation_sandbox import undo
from yaffo.background_tasks.automation_sandbox.automation_host import (
    HOST_API, HostCall, HostFunction, build_host_functions,
    build_recording_host_functions, host_api, render_host_api, summarize_call,
)
from yaffo.background_tasks.automation_sandbox.host_types import resolve_references
from yaffo.background_tasks.automation_sandbox.starlark_runner import RunLimits, run_starlark
from yaffo.db import db
from yaffo.db.models import Album, ApplicationSettings, Face, MediaItem, Person, PersonFace, Tag, FACE_STATUS_UNASSIGNED
from yaffo.db.repositories import album_repository

pytestmark = pytest.mark.unit


@pytest.fixture
def session(tmp_path, monkeypatch):
    monkeypatch.setattr(actions, "emit_event", lambda *args: None)
    engine = create_engine(f"sqlite:///{tmp_path / 'sandbox.db'}")
    db.metadata.create_all(engine)
    with Session(engine) as session:
        session.add_all([MediaItem(id=i, full_file_path=f"/lib/{i}.jpg") for i in range(1, 4)])
        session.commit()
        yield session
    engine.dispose()


def apply(calls, session):
    functions = build_host_functions(session, profile="assistant")
    for call in calls:
        functions[call.name](*call.args)


def test_runaway_loop_is_killed_and_next_run_works():
    started = time.monotonic()
    result = run_starlark("for i in range(1000000000):\n    x = i * 2", limits=RunLimits(timeout_seconds=0.3))
    assert not result.success and "time limit" in result.error
    assert time.monotonic() - started < 5
    assert run_starlark("2 + 2").value == 4


def test_host_budget_stops_before_extra_side_effects():
    calls = []
    result = run_starlark("for i in range(10):\n    record(i)", functions={"record": calls.append},
                          limits=RunLimits(max_host_calls=3))
    assert not result.success and "host-call limit" in result.error
    assert calls == [0, 1, 2]


def test_print_budget_retains_only_bounded_output():
    result = run_starlark("for i in range(10):\n    print('hello')", limits=RunLimits(max_output_chars=12))
    assert not result.success and "output limit" in result.error
    assert result.output == ["hello", "hello"]


def test_large_result_is_rejected():
    result = run_starlark("'x' * 10000", limits=RunLimits(max_message_bytes=1024))
    assert not result.success and "message size" in result.error


def test_deadline_stops_further_calls_after_slow_host():
    called = []
    def slow():
        time.sleep(0.3)
    result = run_starlark("slow()\nrecord()", functions={"slow": slow, "record": lambda: called.append(True)},
                          limits=RunLimits(timeout_seconds=0.2))
    assert not result.success and "time limit" in result.error
    assert not called


def test_profiles_are_explicit_and_docs_match_runtime():
    for profile in ("assistant", "automation"):
        declared = host_api(profile)
        assert set(build_host_functions(object(), profile=profile)) == {fn.name for fn in declared}
        docs = render_host_api(profile)
        assert all(fn.signature in docs for fn in declared)
    assert "report_progress" not in build_host_functions(object(), profile="assistant")
    assert "report_progress(" not in render_host_api("assistant")
    with pytest.raises(ValueError):
        build_host_functions(object(), profile="typo")
    assert HostFunction(lambda s: None, "", "").profiles == frozenset({"automation"})
    forbidden = {"get_api_key", "run_script", "exec", "open", "share", "pair", "set_media_dirs"}
    assert not forbidden & {fn.name for fn in host_api("assistant")}
    for fn in host_api("assistant"):
        if fn.mutating:
            assert fn.setting_key and fn.risk in {"low", "medium", "high"}
    assert next(fn for fn in HOST_API if fn.name == "delete_media_items").risk == "high"


def test_preview_album_reference_uses_mutation_index_and_does_not_write(session):
    functions, calls = build_recording_host_functions(session, profile="assistant")
    result = run_starlark('''data_query({"source": "albums"})
new_album = create_album("Trip")
add_to_album(new_album, [1, 2])
new_album''', functions=functions)
    assert result.success, result.error
    assert result.value == "$ref:0"
    assert calls[-1].args == ["$ref:0", [1, 2]]
    assert session.query(Album).count() == 0
    mutations = calls[1:]
    assert summarize_call(calls[-1], session, mutations) == "Add 2 photo(s) to the new album 'Trip'"
    assert resolve_references(calls[-1].args, {0: 42}) == [42, [1, 2]]


@pytest.mark.parametrize("token", ["$ref:9", "$ref:-1", "$ref:00", "$ref:0suffix"])
def test_invalid_references_fail_closed(session, token):
    functions, calls = build_recording_host_functions(session)
    with pytest.raises(ValueError):
        functions["add_to_album"](token, [1])
    assert calls == []


def test_reference_read_and_wrong_arity_fail(session):
    functions, calls = build_recording_host_functions(session)
    ref = functions["create_album"]("Trip")
    with pytest.raises(ValueError):
        functions["data_query"]({"source": "albums", "id": {"eq": ref}})
    with pytest.raises(TypeError):
        functions["add_to_album"](ref)
    assert len(calls) == 1


def test_recorded_args_are_frozen(session):
    functions, calls = build_recording_host_functions(session)
    tags = [{"media_item_id": 1, "name": "one"}]
    functions["tag_media_items"](tags)
    tags[0]["name"] = "two"
    assert calls[0].args[0][0]["name"] == "one"


def test_query_rows_and_facets_are_bounded(session):
    session.add_all([Tag(media_item_id=1, tag_name=f"tag{i}") for i in range(5010)])
    session.commit()
    for query in [{"source": "tags"}, {"source": "tags", "limit": 99999},
                  {"source": "tags", "op": "facet", "field": "tag_name"}]:
        assert len(actions.data_query(session, query)) == 5000
    assert actions.data_query(session, {"source": "tags", "op": "count"}) == 5010
    with pytest.raises(ValueError):
        actions.data_query(session, {"source": "tags", "limit": -1})


def test_tag_inverse_preserves_preexisting_tags(session):
    original = {"media_item_id": 1, "name": "old"}
    added = {"media_item_id": 1, "name": "new"}
    actions.tag_media_items(session, [original])
    inverse = undo.tag_media_items([[original, added]], session)
    actions.tag_media_items(session, [original, added])
    apply(inverse, session)
    assert [t.tag_name for t in session.query(Tag)] == ["old"]
    inverse = undo.untag_media_items([[original]], session)
    actions.untag_media_items(session, [original])
    apply(inverse, session)
    assert session.query(Tag).one().tag_name == "old"


def test_face_inverse_preserves_existing_assignment_and_later_edits(session):
    session.add_all([Person(id=1, name="One"), Person(id=2, name="Two")])
    session.add_all([Face(id=i, media_item_id=1, status=FACE_STATUS_UNASSIGNED) for i in (1, 2)])
    session.commit()
    actions.assign_faces(session, [{"face_id": 1, "person_id": 1}])
    assignments = [{"face_id": i, "person_id": 1} for i in (1, 2)]
    inverse = undo.assign_faces([assignments], session)
    actions.assign_faces(session, assignments)
    actions.unassign_faces(session, [{"face_id": 2}])
    actions.assign_faces(session, [{"face_id": 2, "person_id": 2}])
    apply(inverse, session)
    assert session.get(PersonFace, 1).person_id == 1
    assert session.get(PersonFace, 2).person_id == 2


@pytest.mark.parametrize("field, action, old, new, later", [
    ("favorite", "set_favorites", None, True, False),
    ("date", "set_media_dates", None, "2024-06-01T12:00:00", "2025-01-02"),
    ("location_name", "set_location_names", "Old", "New", "Later"),
])
def test_value_inverse_restores_each_item_and_skips_drift(session, field, action, old, new, later):
    fn = getattr(actions, action)
    fn(session, [{"id": i, field: old} for i in (1, 2)])
    entries = [{"id": i, field: new} for i in (1, 2)]
    inverse = getattr(undo, action)([entries], session)
    fn(session, entries)
    fn(session, [{"id": 2, field: later}])
    apply(inverse, session)
    column = "date_taken" if field == "date" else field
    assert getattr(session.get(MediaItem, 1), column) == old
    assert getattr(session.get(MediaItem, 2), column) == later
    if field == "date":
        assert session.get(MediaItem, 1).year is None
        assert session.get(MediaItem, 2).year == 2025


def test_date_batch_validates_before_writing(session):
    with pytest.raises(ValueError):
        actions.set_media_dates(session, [{"id": 1, "date": "2024-01-01"}, {"id": 2, "date": "bad"}])
    assert session.get(MediaItem, 1).date_taken is None


def test_album_inverse_skips_existing_and_restores_positions(session):
    album = actions.create_album(session, "Trip")
    assert undo.create_album(["Trip"], session) == []
    actions.add_to_album(session, album, [1, 2, 3])
    inverse = undo.remove_from_album([album, [2]], session)
    actions.remove_from_album(session, album, [2])
    apply(inverse, session)
    assert [m.id for m in album_repository.list_items(session, album)] == [1, 2, 3]
    inverse = undo.add_to_album([album, [1, 2]], session)
    assert inverse == []
    inverse = undo.update_album([album, "Changed"], session)
    actions.update_album(session, album, "Changed")
    apply(inverse, session)
    assert session.get(Album, album).name == "Trip"
    inverse = undo.create_album(["New"], session)
    new_id = actions.create_album(session, "New")
    apply(undo.resolve_undo_result(inverse, new_id), session)
    assert session.get(Album, new_id) is None


def test_new_album_undo_preserves_later_membership(session):
    inverse = undo.create_album(["New"], session)
    album = actions.create_album(session, "New")
    actions.add_to_album(session, album, [3])
    apply(undo.resolve_undo_result(inverse, album), session)
    assert session.get(Album, album) is not None


def test_removed_album_cover_is_restored(session):
    album = actions.create_album(session, "Trip")
    actions.add_to_album(session, album, [1, 2, 3])
    album_repository.set_cover(session, album, 2)
    inverse = undo.remove_from_album([album, [2]], session)
    actions.remove_from_album(session, album, [2])
    apply(inverse, session)
    assert session.get(Album, album).cover_media_item_id == 2


def test_album_undo_preserves_later_position_and_name(session):
    album = actions.create_album(session, "Trip")
    inverse = undo.add_to_album([album, [1, 2]], session)
    actions.add_to_album(session, album, [1, 2])
    album_repository.reorder(session, album, [2, 1])
    apply(inverse, session)
    assert [m.id for m in album_repository.list_items(session, album)] == [2, 1]
    inverse = undo.update_album([album, "Changed"], session)
    actions.update_album(session, album, "Changed")
    actions.update_album(session, album, "Later")
    apply(inverse, session)
    assert session.get(Album, album).name == "Later"


def test_read_host_exceptions_keep_partial_output():
    def fail():
        raise ValueError("expected failure")
    result = run_starlark("print('before')\nfail()", functions={"fail": fail})
    assert not result.success
    assert result.output == ["before"]
    assert "expected failure" in result.error


def test_evaluator_handles_windowed_stdio():
    request = {"code": "2 + 3", "inputs": {}, "functions": [],
               "filename": "test.star", "max_message_bytes": 4096}
    child = subprocess.run(
        [sys.executable, "-c", "import sys; from yaffo.starlark_worker import main; sys.stdin = sys.stdout = None; main()"],
        input=json.dumps(request) + "\n", capture_output=True, text=True, timeout=5,
    )
    assert child.returncode == 0, child.stderr
    assert json.loads(child.stdout)["value"] == 5


def test_unassign_undo_restores_similarity(session):
    session.add(Person(id=1, name="One"))
    session.add(Face(id=1, media_item_id=1, status=FACE_STATUS_UNASSIGNED))
    session.commit()
    actions.assign_faces(session, [{"face_id": 1, "person_id": 1, "similarity": 0.9}])
    inverse = undo.unassign_faces([[{"face_id": 1}]], session)
    actions.unassign_faces(session, [{"face_id": 1}])
    apply(inverse, session)
    assert session.get(PersonFace, 1).similarity == 0.9


def test_folder_read_cap_rejects_inaccurate_partial_counts(session, tmp_path):
    root = tmp_path / "photos"
    session.add(ApplicationSettings(name="media_dirs", type="json", value=json.dumps([
        {"id": "root", "path": str(root)}])))
    session.add_all([MediaItem(full_file_path=str(root / "trip" / f"{i}.jpg")) for i in range(5001)])
    session.commit()
    with pytest.raises(ValueError, match="Folder query row limit"):
        actions.data_query(session, {"source": "folders", "media_dir_id": "root"})
