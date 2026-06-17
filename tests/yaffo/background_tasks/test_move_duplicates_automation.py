"""Tests for the duplicates_found → move-duplicates path:

- find_duplicates._resolve_group_photo_ids: turns the JobResult's path groups into
  ordered photo-id groups (keeper first), keeping paths out of the event payload.
- the seeded _DEDUPE_CODE Starlark run through the real sandbox: keeps group[0] and
  moves the rest into "_Duplicates" via the host API.
"""
from types import SimpleNamespace

import pytest

from yaffo.background_tasks.automation_sandbox.starlark_runner import run_starlark
from yaffo.background_tasks.tasks.find_duplicates import _resolve_group_photo_ids
from yaffo.scripts.seed_automations import _DEDUPE_CODE

pytestmark = pytest.mark.unit


class _FakeSession:
    """Resolves Photo.id/full_file_path queries from a path->id dict, ignoring the
    in_() filter (the resolver passes the candidate paths and we already hold them)."""
    def __init__(self, path_to_id):
        self._rows = [(pid, path) for path, pid in path_to_id.items()]

    def query(self, *cols):
        session_rows = self._rows
        return SimpleNamespace(filter=lambda *a, **k: SimpleNamespace(all=lambda: session_rows))


def test_resolve_group_photo_ids_orders_keeper_first_and_drops_singletons():
    groups = [
        {"id": 0, "paths": ["/m/c.jpg", "/m/a.jpg", "/m/b.jpg"]},  # ids 3,1,2 → sorted 1,2,3
        {"id": 1, "paths": ["/m/x.jpg", "/m/missing.jpg"]},        # one indexed → dropped
    ]
    path_to_id = {"/m/a.jpg": 1, "/m/b.jpg": 2, "/m/c.jpg": 3, "/m/x.jpg": 9}

    resolved = _resolve_group_photo_ids(_FakeSession(path_to_id), groups)

    assert resolved == [[1, 2, 3]]  # keeper (1) first; the single-indexed group dropped


def test_resolve_group_photo_ids_empty():
    assert _resolve_group_photo_ids(_FakeSession({}), []) == []


def _run_dedupe(groups, rows):
    """Run the seeded Starlark with ctx.groups + a stub data_query, capturing moves."""
    moves = []
    functions = {
        "data_query": lambda query: rows,
        "move_photo": lambda photo_id, media_dir_id, target_path: moves.append(
            (photo_id, media_dir_id, target_path)
        ),
    }
    result = run_starlark(_DEDUPE_CODE, inputs={"ctx": {"groups": groups}}, functions=functions)
    assert result.success is True, result.error
    return moves


def test_dedupe_moves_all_but_keeper():
    # Two duplicate sets; keepers are 1 and 4. Non-keepers 2,3,5 move to _Duplicates.
    rows = [
        {"id": 2, "media_dir_id": "mdA"},
        {"id": 3, "media_dir_id": "mdA"},
        {"id": 5, "media_dir_id": "mdB"},
    ]
    moves = _run_dedupe([[1, 2, 3], [4, 5]], rows)
    assert moves == [(2, "mdA", "_Duplicates"), (3, "mdA", "_Duplicates"), (5, "mdB", "_Duplicates")]


def test_dedupe_skips_rows_without_media_dir():
    rows = [{"id": 2, "media_dir_id": None}, {"id": 3, "media_dir_id": "mdA"}]
    moves = _run_dedupe([[1, 2, 3]], rows)
    assert moves == [(3, "mdA", "_Duplicates")]


def test_dedupe_no_groups_is_noop():
    assert _run_dedupe([], []) == []


def test_dedupe_single_photo_group_is_noop():
    # A group with only the keeper has nothing to move.
    assert _run_dedupe([[7]], []) == []
