"""Data-query contract + validation for the page builder.

A widget declares a `data_query`: a dict of **named queries**, each a `source`
(a single table) plus equality filters on that table's columns. The model emits
these (create_widget / update_widget / run_data_query), so before resolving we
validate them against a JSON Schema that is **strict and fails closed**: unknown
sources or unknown/extra fields are rejected, never silently ignored.

The contract is **derived from the SQLAlchemy models**, not hand-maintained:
every primitive column of an exposed table is both a filterable input and a
returned field. There are no joins — a widget runs several named queries and
stitches them together in JavaScript on the id / foreign-key columns. Adding a
column to a model therefore widens the query surface automatically; the only
hand-maintained parts are the table whitelist and a small per-table denylist.

This module owns the schema + validation; query *resolution* (the single-table
SQLAlchemy reads + serialization) will live alongside it as the repo grows.
"""
from __future__ import annotations

import datetime
from typing import Any, Optional

from jsonschema import Draft202012Validator
from sqlalchemy import Select, distinct, func, select
from sqlalchemy.orm import Session

from yaffo.db.models import Face, Person, PersonFace, Photo, Tag

_DRAFT = "https://json-schema.org/draft/2020-12/schema"

# Single-table sources exposed to widgets. Infra tables (jobs, settings, …) are
# deliberately absent. Each table's primitive columns become the query surface.
_EXPOSED_MODELS = [Photo, Tag, Face, Person, PersonFace]

# Primitive columns to hide per model. Filesystem paths leak the local disk
# layout, and images load via the /photos/<id> route, so the path isn't needed.
_DENY: dict[type, set[str]] = {
    Photo: {"full_file_path"},
    Face: {"full_file_path"},
}

# Reserved query keys (directives, not columns); they win over any same-named
# column so they can't be shadowed.
_LIMIT = {"type": "integer", "minimum": 1, "description": "Max rows to return."}


def _json_type(column: Any) -> Optional[dict]:
    """Map a SQLAlchemy column to a JSON Schema type, or None to exclude it
    (LargeBinary/embeddings and any non-primitive type are excluded)."""
    try:
        py = column.type.python_type
    except (NotImplementedError, AttributeError):
        return None
    if py is bool:  # before int — bool is an int subclass in Python
        return {"type": "boolean"}
    if py is int:
        return {"type": "integer"}
    if py is float:
        return {"type": "number"}
    if py is str:
        return {"type": "string"}
    if py is datetime.datetime:
        return {"type": "string", "description": "ISO datetime."}
    if py is datetime.date:
        return {"type": "string", "description": "ISO date."}
    return None


def _source_fields(model: type) -> dict[str, dict]:
    deny = _DENY.get(model, set())
    fields: dict[str, dict] = {}
    for column in model.__table__.columns:
        if column.name in deny:
            continue
        json_type = _json_type(column)
        if json_type is not None:
            fields[column.name] = json_type
    return fields


# {source_table: {column: json_type_schema}} — each source's column shapes (the
# returned-field types, and the basis the query filters are built from).
FIELDS_BY_SOURCE: dict[str, dict[str, dict]] = {
    model.__tablename__: _source_fields(model) for model in _EXPOSED_MODELS
}
SOURCES = tuple(FIELDS_BY_SOURCE)


def _filter_schema(json_type: str) -> dict:
    """The filter a column accepts: an object of type-appropriate operators (a
    column is filtered as `{"<op>": value}`). Closed + non-empty so unknown
    operators and empty filters fail."""
    value = {"type": json_type}
    in_list = {"type": "array", "items": value, "minItems": 1}
    if json_type == "boolean":
        operators = {"eq": value, "ne": value}
    elif json_type in ("integer", "number"):
        operators = {op: value for op in ("eq", "ne", "lt", "lte", "gt", "gte")}
        operators["in"] = in_list
    else:  # string
        operators = {op: value for op in ("eq", "ne", "contains")}
        operators["in"] = in_list
    return {
        "type": "object",
        "properties": operators,
        "additionalProperties": False,
        "minProperties": 1,
    }


# Aggregate operations: `count` (rows total, no field), `count_distinct` (distinct
# values of a field), `facet` (GROUP BY field -> [{value, count}]), `range`
# (min/max of a field). All but `count` require `field`.
AGGREGATE_OPS = ("count", "count_distinct", "facet", "range")


def _filters_for(fields: dict[str, dict]) -> dict[str, dict]:
    return {column: _filter_schema(schema["type"]) for column, schema in fields.items()}


def _rows_branch(source: str, fields: dict[str, dict]) -> dict:
    """A row-fetch query: a discriminating `source` const, per-column operator
    filters, `limit`, and nothing else (fail closed)."""
    properties = _filters_for(fields)
    properties["source"] = {"const": source}
    properties["limit"] = _LIMIT
    return {
        "type": "object",
        "properties": properties,
        "required": ["source"],
        "additionalProperties": False,
    }


def _aggregate_branch(source: str, fields: dict[str, dict]) -> dict:
    """An aggregate query: `source` const, `op`, a `field` (required for every op
    but `count`, forbidden for `count`), plus the same operator filters as an
    optional WHERE."""
    properties = _filters_for(fields)
    properties["source"] = {"const": source}
    properties["op"] = {"enum": list(AGGREGATE_OPS)}
    properties["field"] = {"enum": list(fields)}
    return {
        "type": "object",
        "properties": properties,
        "required": ["source", "op"],
        "additionalProperties": False,
        "allOf": [
            {"if": {"required": ["op"], "properties": {"op": {"enum": ["count_distinct", "facet", "range"]}}},
             "then": {"required": ["field"]}},
            {"if": {"required": ["op"], "properties": {"op": {"const": "count"}}},
             "then": {"not": {"required": ["field"]}}},
        ],
    }


# The published schema is the `oneOf` over every source's rows branch and
# aggregate branch. Validation dispatches to the single matching branch (by source
# + whether `op` is present) so errors are precise rather than oneOf's opaque
# "not valid under any of the given schemas".
_ROWS_VALIDATORS = {s: Draft202012Validator(_rows_branch(s, f)) for s, f in FIELDS_BY_SOURCE.items()}
_AGG_VALIDATORS = {s: Draft202012Validator(_aggregate_branch(s, f)) for s, f in FIELDS_BY_SOURCE.items()}

_QUERY = {"oneOf": [
    *(_rows_branch(s, f) for s, f in FIELDS_BY_SOURCE.items()),
    *(_aggregate_branch(s, f) for s, f in FIELDS_BY_SOURCE.items()),
]}

QUERY_SCHEMA: dict[str, Any] = {"$schema": _DRAFT, **_QUERY}

# A data_query: a non-empty dict of named queries, each value a single query.
DATA_QUERY_SCHEMA: dict[str, Any] = {
    "$schema": _DRAFT,
    "type": "object",
    "description": "Named queries: each key is a query name you choose; each value is one query.",
    "minProperties": 1,
    "additionalProperties": _QUERY,
}


def _format_errors(validator: Draft202012Validator, instance: Any) -> list[str]:
    errors = []
    for err in validator.iter_errors(instance):
        loc = "/".join(str(p) for p in err.absolute_path)
        errors.append(f"{loc}: {err.message}" if loc else err.message)
    return errors


def validate_query(query: Any) -> list[str]:
    """Validate a single query against its source's rows or aggregate branch (an
    `op` key selects aggregate), so errors name the offending field/type. Returns
    human-readable errors; empty means valid."""
    if not isinstance(query, dict):
        return ["query must be an object"]
    source = query.get("source")
    if source not in FIELDS_BY_SOURCE:
        if source is None:
            return [f"missing 'source' (one of: {', '.join(SOURCES)})"]
        return [f"unknown source '{source}' (valid: {', '.join(SOURCES)})"]
    validator = _AGG_VALIDATORS[source] if "op" in query else _ROWS_VALIDATORS[source]
    return _format_errors(validator, query)


def validate_data_query(data_query: Any) -> list[str]:
    """Validate a full data_query (dict of named queries), prefixing each error
    with its query name. Returns human-readable errors; empty means valid."""
    if not isinstance(data_query, dict):
        return ["data_query must be an object of named queries"]
    if not data_query:
        return ["data_query must contain at least one named query"]
    errors = []
    for name, query in data_query.items():
        errors.extend(f"{name}: {err}" for err in validate_query(query))
    return errors


# ---------------------------------------------------------------------------
# Resolution: data_query -> SQL
# ---------------------------------------------------------------------------

_MODEL_BY_SOURCE = {model.__tablename__: model for model in _EXPOSED_MODELS}

# Operator -> SQLAlchemy column expression. Mirrors the operators advertised per
# column type in `_filter_schema`.
_OPERATORS = {
    "eq": lambda column, value: column == value,
    "ne": lambda column, value: column != value,
    "lt": lambda column, value: column < value,
    "lte": lambda column, value: column <= value,
    "gt": lambda column, value: column > value,
    "gte": lambda column, value: column >= value,
    "in": lambda column, value: column.in_(value),
    "contains": lambda column, value: column.contains(value),
}

# Keys that are query directives, not column filters.
_RESERVED_KEYS = ("source", "op", "field", "limit")


def _where_conditions(table, query: dict) -> list:
    return [
        _OPERATORS[op](table.c[column], operand)
        for column, filters in query.items()
        if column not in _RESERVED_KEYS
        for op, operand in filters.items()
    ]


def _aggregate_select(table, query: dict, conditions: list) -> Select:
    op = query["op"]
    column = table.c[query["field"]] if "field" in query else None
    if op == "count":
        stmt = select(func.count()).select_from(table)
    elif op == "count_distinct":
        stmt = select(func.count(distinct(column)))
    elif op == "facet":
        stmt = select(column.label("value"), func.count().label("count")).group_by(column)
    elif op == 'range':
        stmt = select(func.min(column).label("min"), func.max(column).label("max"))
    else:
        raise ValueError(f"unknown operator: {op}")
    return stmt.where(*conditions) if conditions else stmt


def build_query(query: dict) -> Select:
    """Translate one query into a SQLAlchemy Select over its source table. A rows
    query selects the source's exposed columns with the per-column operator
    filters + `limit`; an aggregate query (`op` present) builds the count / facet /
    range. Pure translation — assumes the query already passed `validate_query`."""
    table = _MODEL_BY_SOURCE[query["source"]].__table__
    conditions = _where_conditions(table, query)
    if "op" in query:
        return _aggregate_select(table, query, conditions)
    stmt = select(*(table.c[name] for name in FIELDS_BY_SOURCE[query["source"]]))
    if conditions:
        stmt = stmt.where(*conditions)
    if "limit" in query:
        stmt = stmt.limit(query["limit"])
    return stmt


def resolve_query(session: Session, query: dict) -> Any:
    """Validate, translate, and run one query. The return shape depends on the
    query: rows -> list of column dicts; `count`/`count_distinct` -> a number;
    `facet` -> list of {value, count}; `range` -> {min, max}. Raises ValueError if
    the query is invalid."""
    errors = validate_query(query)
    if errors:
        raise ValueError("; ".join(errors))
    stmt = build_query(query)
    op = query.get("op")
    if op in ("count", "count_distinct"):
        return session.execute(stmt).scalar()
    if op == "range":
        return dict(session.execute(stmt).mappings().one())
    # rows and facet both come back as a list of dict rows
    return [dict(row) for row in session.execute(stmt).mappings().all()]


def resolve_data_query(session: Session, data_query: dict) -> dict[str, Any]:
    """Validate and resolve a full data_query; return {query_name: result}, each
    result shaped per `resolve_query`."""
    errors = validate_data_query(data_query)
    if errors:
        raise ValueError("; ".join(errors))
    return {name: resolve_query(session, query) for name, query in data_query.items()}