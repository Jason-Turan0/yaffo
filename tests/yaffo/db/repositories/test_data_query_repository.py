"""Unit tests for the data-query contract derived from the SQLAlchemy models.

Covers the two things this repository guarantees: (1) the schema is derived from
the models correctly — every primitive column exposed, blobs/denied columns
hidden, types mapped — and (2) validation is strict and fails closed with precise,
per-source error messages.
"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects import sqlite as sqlite_dialect
from sqlalchemy.orm import Session

from yaffo.db import db
from yaffo.db.models import Person, Photo
from yaffo.db.repositories import data_query_repository as dq

pytestmark = pytest.mark.unit


def _sql(query: dict) -> str:
    """The generated SQL for a query, with literal values inlined and whitespace
    normalized, so tests can assert on it directly."""
    compiled = dq.build_query(query).compile(
        dialect=sqlite_dialect.dialect(), compile_kwargs={"literal_binds": True}
    )
    return " ".join(str(compiled).split())


@pytest.fixture
def session(tmp_path):
    """A throwaway SQLite database (temp file) with the real model tables and a
    small seeded dataset, for exercising resolution end to end."""
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    db.metadata.create_all(engine)
    with Session(engine) as sess:
        sess.add_all([
            Person(id=1, name="Obama"),
            Person(id=2, name="Michelle"),
            Photo(id=11, year=2021, location_name="Acadia NP"),
            Photo(id=12, year=2023, location_name="Bar Harbor"),
            Photo(id=13, year=2023, location_name="Camden"),
        ])
        sess.commit()
        yield sess
    engine.dispose()


class TestSchemaDerivation:
    """The query surface is introspected from the exposed models."""

    def test_sources_are_the_exposed_tables(self):
        assert dq.SOURCES == ("photos", "tags", "faces", "people", "people_face")

    def test_photos_exposes_primitive_columns(self):
        fields = dq.FIELDS_BY_SOURCE["photos"]
        assert "id" in fields
        assert "year" in fields
        assert "location_name" in fields
        assert "status" in fields

    def test_column_types_are_mapped(self):
        fields = dq.FIELDS_BY_SOURCE["photos"]
        assert fields["id"]["type"] == "integer"
        assert fields["year"]["type"] == "integer"
        assert fields["latitude"]["type"] == "number"
        assert fields["location_name"]["type"] == "string"

    def test_denied_filesystem_paths_are_hidden(self):
        assert "full_file_path" not in dq.FIELDS_BY_SOURCE["photos"]
        assert "full_file_path" not in dq.FIELDS_BY_SOURCE["faces"]

    def test_binary_columns_are_excluded(self):
        # LargeBinary embeddings must never reach the model.
        assert "embedding" not in dq.FIELDS_BY_SOURCE["faces"]
        assert "avg_embedding" not in dq.FIELDS_BY_SOURCE["people"]

    def test_foreign_keys_are_exposed_for_js_joins(self):
        # The model joins in JS, so the link columns must be queryable/returned.
        assert dq.FIELDS_BY_SOURCE["tags"]["photo_id"]["type"] == "integer"
        assert dq.FIELDS_BY_SOURCE["people_face"]["person_id"]["type"] == "integer"
        assert dq.FIELDS_BY_SOURCE["people_face"]["face_id"]["type"] == "integer"

    def test_infra_tables_are_not_exposed(self):
        assert "jobs" not in dq.SOURCES
        assert "application_settings" not in dq.SOURCES


class TestSchemaShape:
    """The published JSON Schema is a strict, fail-closed per-source union."""

    def test_query_schema_has_rows_and_aggregate_branch_per_source(self):
        assert len(dq.QUERY_SCHEMA["oneOf"]) == 2 * len(dq.SOURCES)

    def test_branches_are_closed_and_require_source(self):
        for branch in dq.QUERY_SCHEMA["oneOf"]:
            assert branch["additionalProperties"] is False
            assert "source" in branch["required"]

    def test_data_query_requires_at_least_one_named_query(self):
        assert dq.DATA_QUERY_SCHEMA["minProperties"] == 1


class TestValidateQuery:
    """Single-query validation dispatches to the matching source branch."""

    def test_valid_query(self):
        assert dq.validate_query({"source": "photos", "year": {"eq": 2023}, "limit": 9}) == []

    def test_limit_allowed_on_every_source(self):
        for source in dq.SOURCES:
            assert dq.validate_query({"source": source, "limit": 5}) == []

    def test_unknown_source_names_valid_sources(self):
        errors = dq.validate_query({"source": "albums"})
        assert len(errors) == 1
        assert "unknown source 'albums'" in errors[0]
        assert "photos" in errors[0]

    def test_missing_source(self):
        errors = dq.validate_query({"limit": 5})
        assert errors and "missing 'source'" in errors[0]

    def test_unknown_field_rejected(self):
        errors = dq.validate_query({"source": "photos", "colour": {"eq": "red"}})
        assert errors and "colour" in errors[0]

    def test_limit_minimum_enforced(self):
        errors = dq.validate_query({"source": "people", "limit": 0})
        assert errors and "limit" in errors[0]

    def test_non_dict_query(self):
        assert dq.validate_query("photos") == ["query must be an object"]


class TestColumnFilters:
    """Per-column operator filters, typed and fail-closed."""

    def test_comparison_operators_on_numbers(self):
        assert dq.validate_query({"source": "photos", "year": {"gte": 2020, "lte": 2023}}) == []

    def test_in_operator(self):
        assert dq.validate_query({"source": "photos", "year": {"in": [2021, 2022]}}) == []

    def test_contains_operator_on_strings(self):
        assert dq.validate_query({"source": "people", "name": {"contains": "Mich"}}) == []

    def test_bare_scalar_is_rejected(self):
        # A filter must be an operator object, not a bare value.
        errors = dq.validate_query({"source": "photos", "year": 2023})
        assert errors == ["year: 2023 is not of type 'object'"]

    def test_operator_value_type_is_checked(self):
        errors = dq.validate_query({"source": "photos", "year": {"gte": "x"}})
        assert errors == ["year/gte: 'x' is not of type 'integer'"]

    def test_unknown_operator_rejected(self):
        errors = dq.validate_query({"source": "photos", "year": {"between": [1, 2]}})
        assert errors and "between" in errors[0]

    def test_empty_filter_rejected(self):
        errors = dq.validate_query({"source": "photos", "year": {}})
        assert errors and "year" in errors[0]

    def test_comparison_not_offered_on_strings(self):
        # `gte` is a numeric operator; on a string column it isn't allowed.
        errors = dq.validate_query({"source": "people", "name": {"gte": "a"}})
        assert errors and "gte" in errors[0]

    def test_contains_not_offered_on_numbers(self):
        errors = dq.validate_query({"source": "photos", "year": {"contains": 2023}})
        assert errors and "contains" in errors[0]


class TestValidateDataQuery:
    """Named-query validation prefixes each error with its query name."""

    def test_valid_data_query(self):
        assert dq.validate_data_query({"g": {"source": "photos", "year": {"eq": 2023}}}) == []

    def test_js_join_pattern_is_valid(self):
        # Separate named queries the widget stitches together in JS.
        data_query = {
            "photos": {"source": "photos", "year": {"eq": 2023}},
            "links": {"source": "people_face"},
            "people": {"source": "people"},
        }
        assert dq.validate_data_query(data_query) == []

    def test_empty_data_query_rejected(self):
        assert dq.validate_data_query({}) == ["data_query must contain at least one named query"]

    def test_non_dict_data_query_rejected(self):
        assert dq.validate_data_query([]) == ["data_query must be an object of named queries"]

    def test_errors_are_prefixed_with_query_name(self):
        errors = dq.validate_data_query({"maine": {"source": "photos", "year": {"gte": "x"}}})
        assert errors == ["maine: year/gte: 'x' is not of type 'integer'"]

    def test_errors_from_multiple_queries_all_surface(self):
        errors = dq.validate_data_query({
            "a": {"source": "photos", "colour": {"eq": "red"}},
            "b": {"source": "nope"},
        })
        assert any(e.startswith("a:") for e in errors)
        assert any(e.startswith("b:") and "unknown source" in e for e in errors)


class TestGeneratedSql:
    """A query translates to the expected SQLAlchemy SELECT."""

    def test_selects_the_sources_exposed_columns(self):
        assert _sql({"source": "people"}) == "SELECT people.id, people.name FROM people"

    def test_eq_filter_and_limit(self):
        sql = _sql({"source": "people", "name": {"eq": "Obama"}, "limit": 5})
        assert "FROM people WHERE people.name = 'Obama'" in sql
        assert "LIMIT 5" in sql

    def test_range_operators_are_anded(self):
        sql = _sql({"source": "photos", "year": {"gte": 2020, "lte": 2023}})
        assert "WHERE photos.year >= 2020 AND photos.year <= 2023" in sql

    def test_multiple_columns_are_anded(self):
        sql = _sql({"source": "photos", "year": {"eq": 2023}, "location_name": {"eq": "Camden"}})
        assert "photos.year = 2023 AND photos.location_name = 'Camden'" in sql

    def test_in_operator(self):
        assert "photos.id IN (11, 12)" in _sql({"source": "photos", "id": {"in": [11, 12]}})

    def test_contains_uses_like(self):
        sql = _sql({"source": "people", "name": {"contains": "Mich"}})
        assert "LIKE" in sql and "'Mich'" in sql

    def test_no_filters_yields_no_where(self):
        sql = _sql({"source": "tags"})
        assert sql == "SELECT tags.id, tags.photo_id, tags.tag_name, tags.tag_value FROM tags"


class TestResolve:
    """Resolution runs the generated SQL against a real (temp) database."""

    def test_eq_filter_returns_matching_rows(self, session):
        rows = dq.resolve_query(session, {"source": "photos", "year": {"eq": 2023}})
        assert {row["id"] for row in rows} == {12, 13}

    def test_rows_expose_exactly_the_source_columns(self, session):
        rows = dq.resolve_query(session, {"source": "people", "id": {"eq": 1}})
        assert rows == [{"id": 1, "name": "Obama"}]

    def test_contains_filter(self, session):
        rows = dq.resolve_query(session, {"source": "people", "name": {"contains": "Mich"}})
        assert [row["name"] for row in rows] == ["Michelle"]

    def test_in_filter(self, session):
        rows = dq.resolve_query(session, {"source": "photos", "id": {"in": [11, 13]}})
        assert {row["id"] for row in rows} == {11, 13}

    def test_limit_is_applied(self, session):
        rows = dq.resolve_query(session, {"source": "photos", "limit": 2})
        assert len(rows) == 2

    def test_resolve_data_query_returns_named_results(self, session):
        result = dq.resolve_data_query(session, {
            "recent": {"source": "photos", "year": {"eq": 2023}},
            "people": {"source": "people"},
        })
        assert set(result) == {"recent", "people"}
        assert {row["id"] for row in result["recent"]} == {12, 13}
        assert len(result["people"]) == 2

    def test_invalid_query_raises(self, session):
        with pytest.raises(ValueError):
            dq.resolve_query(session, {"source": "photos", "colour": {"eq": "red"}})


class TestParameterization:
    """User-supplied filter *values* are bound parameters, never inlined into the
    SQL (no injection). Identifiers (source/field) can't be injected either — they
    are whitelisted by the schema enums and rejected before a statement is built."""

    @staticmethod
    def _compiled(query):
        compiled = dq.build_query(query).compile(dialect=sqlite_dialect.dialect())
        return " ".join(str(compiled).split()), dict(compiled.params)

    def test_string_eq_value_is_bound_not_inlined(self):
        payload = "'; DROP TABLE people; --"
        sql, params = self._compiled({"source": "people", "name": {"eq": payload}})
        assert payload not in sql           # not concatenated into the SQL text
        assert payload in params.values()   # carried as a bound parameter
        assert "?" in sql

    def test_numeric_value_is_bound(self):
        sql, params = self._compiled({"source": "photos", "year": {"eq": 2023}})
        assert "2023" not in sql
        assert 2023 in params.values()

    def test_contains_value_is_bound(self):
        sql, params = self._compiled({"source": "people", "name": {"contains": "O'Brien"}})
        assert "O'Brien" not in sql
        assert "O'Brien" in params.values()

    def test_in_list_values_are_bound(self):
        sql, params = self._compiled({"source": "people", "name": {"in": ["a", "b'; --"]}})
        assert "b'; --" not in sql
        assert ["a", "b'; --"] in params.values()

    def test_aggregate_filter_value_is_bound(self):
        sql, params = self._compiled({"source": "photos", "op": "count", "location_name": {"eq": "Maine"}})
        assert "Maine" not in sql
        assert "Maine" in params.values()

    def test_injection_string_is_treated_as_data(self, session):
        # The malicious value matches no row and, crucially, does not execute:
        # the table is still there afterward.
        malicious = "'; DROP TABLE people; --"
        assert dq.resolve_query(session, {"source": "people", "name": {"eq": malicious}}) == []
        assert dq.resolve_query(session, {"source": "people", "op": "count"}) == 2

    def test_non_whitelisted_field_is_rejected_before_building_sql(self, session):
        # Identifiers aren't parameterizable, so they must be whitelisted instead.
        with pytest.raises(ValueError):
            dq.resolve_query(session, {"source": "photos", "op": "facet", "field": "full_file_path"})


class TestAggregateValidation:
    """Aggregate queries (`op` present) validate against the aggregate branch."""

    def test_count_needs_no_field(self):
        assert dq.validate_query({"source": "photos", "op": "count"}) == []

    def test_count_rejects_field(self):
        assert dq.validate_query({"source": "photos", "op": "count", "field": "year"})

    def test_field_required_for_count_distinct(self):
        errors = dq.validate_query({"source": "photos", "op": "count_distinct"})
        assert errors and "field" in errors[0]

    def test_field_required_for_facet(self):
        assert dq.validate_query({"source": "photos", "op": "facet"})

    def test_field_required_for_range(self):
        assert dq.validate_query({"source": "photos", "op": "range"})

    def test_unknown_op_rejected(self):
        errors = dq.validate_query({"source": "photos", "op": "median", "field": "year"})
        assert errors and "median" in errors[0]

    def test_field_must_be_a_column(self):
        errors = dq.validate_query({"source": "photos", "op": "facet", "field": "colour"})
        assert errors and "colour" in errors[0]

    def test_aggregate_with_filter_is_valid(self):
        query = {"source": "photos", "op": "facet", "field": "location_name", "year": {"eq": 2023}}
        assert dq.validate_query(query) == []


class TestAggregateSql:
    """Aggregate queries translate to the expected SQL."""

    def test_count(self):
        assert "SELECT count(*) AS count_1 FROM photos" in _sql({"source": "photos", "op": "count"})

    def test_count_distinct(self):
        sql = _sql({"source": "photos", "op": "count_distinct", "field": "location_name"})
        assert "count(DISTINCT photos.location_name)" in sql

    def test_facet_groups_by_field(self):
        sql = _sql({"source": "photos", "op": "facet", "field": "year"})
        assert "photos.year AS value, count(*) AS count FROM photos GROUP BY photos.year" in sql

    def test_range(self):
        sql = _sql({"source": "photos", "op": "range", "field": "date_taken"})
        assert "min(photos.date_taken) AS min, max(photos.date_taken) AS max FROM photos" in sql

    def test_aggregate_applies_filters_as_where(self):
        sql = _sql({"source": "photos", "op": "facet", "field": "location_name", "year": {"eq": 2023}})
        assert "WHERE photos.year = 2023 GROUP BY photos.location_name" in sql


class TestAggregateResolve:
    """Aggregates execute and return their op-specific shapes."""

    def test_count(self, session):
        assert dq.resolve_query(session, {"source": "photos", "op": "count"}) == 3

    def test_count_with_filter(self, session):
        assert dq.resolve_query(session, {"source": "photos", "op": "count", "year": {"eq": 2023}}) == 2

    def test_count_distinct(self, session):
        assert dq.resolve_query(session, {"source": "photos", "op": "count_distinct", "field": "year"}) == 2

    def test_facet_returns_value_count_pairs(self, session):
        rows = dq.resolve_query(session, {"source": "photos", "op": "facet", "field": "year"})
        assert {row["value"]: row["count"] for row in rows} == {2021: 1, 2023: 2}

    def test_range(self, session):
        result = dq.resolve_query(session, {"source": "photos", "op": "range", "field": "year"})
        assert result == {"min": 2021, "max": 2023}

    def test_resolve_data_query_mixes_rows_and_aggregates(self, session):
        result = dq.resolve_data_query(session, {
            "total": {"source": "photos", "op": "count"},
            "by_year": {"source": "photos", "op": "facet", "field": "year"},
            "recent": {"source": "photos", "year": {"eq": 2023}},
        })
        assert result["total"] == 3
        assert {row["value"]: row["count"] for row in result["by_year"]} == {2021: 1, 2023: 2}
        assert {row["id"] for row in result["recent"]} == {12, 13}