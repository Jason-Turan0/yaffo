"""Unit tests for the run_data_query tool (yaffo/site_agents/tool_providers/
data_query_tool.py).

The tool previews real query results before the model writes a widget. For the
photos source it enriches each row with the host-derived calculated columns
(media_dir_id / relative_path) so the preview matches what the sandbox host serves
at runtime. resolve_query and the media-dir lookups are monkeypatched so these
exercise the provider's wiring (enrich only for photos rows, preview shape) without
a database or filesystem.
"""
import json
from pathlib import Path

import pytest

from yaffo.site_agents.tool_providers import data_query_tool
from yaffo.utils.settings import MediaDir

pytestmark = pytest.mark.unit


def _patch(monkeypatch, target, fn):
    monkeypatch.setattr(target, fn)


def _provider(monkeypatch, rows):
    """A provider whose resolver returns `rows` for any query (no DB)."""
    _patch(monkeypatch, "yaffo.site_agents.tool_providers.data_query_tool.resolve_query",
           lambda session, args: rows)
    return data_query_tool.DataQueryToolProvider(session=object())


def _patch_media_dirs(monkeypatch, paths, entries):
    _patch(monkeypatch, "yaffo.background_tasks.automation_sandbox.media_dirs."
           "photos_repository.get_paths_by_ids", lambda s, ids: paths)
    _patch(monkeypatch, "yaffo.background_tasks.automation_sandbox.media_dirs."
           "get_media_dir_entries", lambda s: entries)


class TestCalculatedColumns:
    def test_photos_sample_carries_calculated_columns(self, monkeypatch):
        provider = _provider(monkeypatch, [{"id": 1, "year": 2024}])
        _patch_media_dirs(monkeypatch, {1: "/lib/2024/IMG.jpg"},
                          [MediaDir(id="GUID", path=Path("/lib"))])

        result = provider.call_tool("run_data_query", {"source": "photos"})

        sample = json.loads(result)["sample"][0]
        assert sample["media_dir_id"] == "GUID"
        assert sample["relative_path"] == "2024/IMG.jpg"

    def test_photo_outside_media_dirs_gets_null_calculated_columns(self, monkeypatch):
        provider = _provider(monkeypatch, [{"id": 2, "year": 2024}])
        _patch_media_dirs(monkeypatch, {2: "/elsewhere/x.jpg"},
                          [MediaDir(id="GUID", path=Path("/lib"))])

        sample = json.loads(provider.call_tool("run_data_query", {"source": "photos"}))["sample"][0]

        assert sample["media_dir_id"] is None
        assert sample["relative_path"] is None

    def test_non_photos_source_is_not_enriched(self, monkeypatch):
        # Media-dir lookups are left unpatched: if enrich ran here it would reach
        # the real repository, so the assertion also proves it didn't.
        provider = _provider(monkeypatch, [{"id": 1, "name": "Obama"}])

        sample = json.loads(provider.call_tool("run_data_query", {"source": "people"}))["sample"][0]

        assert sample == {"id": 1, "name": "Obama"}

    def test_photos_aggregate_is_not_enriched(self, monkeypatch):
        # An aggregate resolves to a scalar/dict, not a row list, so there's
        # nothing to enrich and the calculated-column path is skipped.
        provider = _provider(monkeypatch, 7)

        payload = json.loads(provider.call_tool("run_data_query", {"source": "photos", "op": "count"}))

        assert payload == {"source": "photos", "data": 7}


class TestSourceSchema:
    def test_photos_schema_lists_columns_with_calculated_appended(self):
        provider = data_query_tool.DataQueryToolProvider(session=object())

        payload = json.loads(provider.call_tool("get_source_schema", {"source": "photos"}))

        assert payload["source"] == "photos"
        fields = payload["fields"]
        assert fields["id"]["type"] == "integer"
        # Calculated columns are appended after the real columns.
        names = list(fields)
        assert names[-2:] == ["media_dir_id", "relative_path"]
        assert fields["relative_path"]["type"] == "string"

    def test_source_without_calculated_columns_lists_only_real_columns(self):
        provider = data_query_tool.DataQueryToolProvider(session=object())

        fields = json.loads(provider.call_tool("get_source_schema", {"source": "people"}))["fields"]

        assert "media_dir_id" not in fields
        assert "relative_path" not in fields

    def test_unknown_source_is_fed_back_to_the_model(self):
        provider = data_query_tool.DataQueryToolProvider(session=object())

        result = provider.call_tool("get_source_schema", {"source": "widgets"})

        assert result.startswith("Invalid source: unknown source 'widgets'")


class TestCallToolContract:
    def test_unknown_tool_name_is_reported(self, monkeypatch):
        provider = _provider(monkeypatch, [])
        assert provider.call_tool("nope", {}) == "Unknown tool: nope"

    def test_invalid_query_is_fed_back_to_the_model(self, monkeypatch):
        def _raise(session, args):
            raise ValueError("unknown source 'widgets'")

        _patch(monkeypatch, "yaffo.site_agents.tool_providers.data_query_tool.resolve_query", _raise)
        provider = data_query_tool.DataQueryToolProvider(session=object())

        assert provider.call_tool("run_data_query", {"source": "widgets"}) == (
            "Invalid query: unknown source 'widgets'"
        )