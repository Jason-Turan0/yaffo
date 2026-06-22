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
from dataclasses import dataclass
from typing import Any, Callable, Optional

from jsonschema import Draft202012Validator
from sqlalchemy import Select, distinct, func, select
from sqlalchemy.orm import Session

from yaffo.db.models import ClassificationLabel, Face, Person, PersonFace, MediaItem, MediaLabel, Tag
from yaffo.db.repositories import media_dir_repository

_DRAFT = "https://json-schema.org/draft/2020-12/schema"

# Single-table sources exposed to widgets. Infra tables (jobs, settings, …) are
# deliberately absent. Each table's primitive columns become the query surface.
# classification_labels + photo_labels expose the auto-classifier's labels read-only
# (data_query is read-only; automations apply their own categorization via tag_media_items),
# joined client-side like people/people_face/faces.
_EXPOSED_MODELS = [MediaItem, Tag, Face, Person, PersonFace, ClassificationLabel, MediaLabel]

# Primitive columns to hide per model. Filesystem paths leak the local disk
# layout, and images load via the /media/<id> route, so the path isn't needed.
_DENY: dict[type, set[str]] = {
    MediaItem: {"full_file_path"},
    Face: {"full_file_path"},
}

# Calculated columns per model: not real DB columns — the host derives them from
# full_file_path (never exposed) and appends them to each row. They're always part of
# a source's returned schema; `queryable` ones may also be filtered (with their own
# restricted `ops`, and any `requires` co-filters), translated to full_file_path SQL
# in media_dir_repository. `prefix` matches a relative-path string prefix (recursive).
_CALCULATED: dict[type, dict[str, Any]] = {
    MediaItem: {
        "media_dir_id": {
            "type": "string", "description": "Media directory id (from the media_dirs source).",
            "queryable": True, "ops": ("eq", "in"),
        },
        "relative_path": {
            "type": "string", "description": "Path to the file relative to its media directory.",
            "queryable": True, "ops": ("eq", "prefix"), "requires": ("media_dir_id",),
        },
    },
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


# {source_table: {column: json_type_schema}} — each table source's column shapes (the
# returned-field types, and the basis the query filters are built from).
FIELDS_BY_SOURCE: dict[str, dict[str, dict]] = {
    model.__tablename__: _source_fields(model) for model in _EXPOSED_MODELS
}

# Calculated columns keyed by source table. Not real DB columns (see _CALCULATED);
# always in source_schema(), only filterable when `queryable`.
CALCULATED_BY_SOURCE: dict[str, dict[str, dict]] = {
    model.__tablename__: _CALCULATED[model] for model in _EXPOSED_MODELS if model in _CALCULATED
}


@dataclass(frozen=True)
class _VirtualSource:
    """A source not backed by a SQLAlchemy table: a JSON Schema for its query (named
    params, not column filters), the `fields` a returned row carries (for
    source_schema / prompts), and a resolver run against the session."""
    schema: dict
    fields: dict
    resolve: Callable[[Session, dict], Any]


def _virtual_schema(source: str, params: dict, required: list[str]) -> dict:
    return {
        "type": "object",
        "properties": {"source": {"const": source}, **params},
        "required": ["source", *required],
        "additionalProperties": False,
    }


# Virtual sources: media dirs and the on-disk folder tree, queried through the same
# query()/run_data_query path as tables (their resolvers live in media_dir_repository).
_VIRTUAL_SOURCES: dict[str, _VirtualSource] = {
    "media_dirs": _VirtualSource(
        schema=_virtual_schema("media_dirs", {}, []),
        fields={
            "id": {"type": "string", "description": "Media directory id."},
            "name": {"type": "string", "description": "Media directory name."},
        },
        resolve=media_dir_repository.resolve_media_dirs,
    ),
    "folders": _VirtualSource(
        schema=_virtual_schema(
            "folders",
            {
                "media_dir_id": {"type": "string", "description": "Media directory id (from media_dirs)."},
                "path": {"type": "string", "description": "Folder path within the dir; omit/'' for the root."},
            },
            ["media_dir_id"],
        ),
        fields={
            "name": {"type": "string", "description": "Immediate subfolder name."},
            "media_count": {"type": "integer", "description": "Photos indexed under that subfolder."},
        },
        resolve=media_dir_repository.resolve_folders,
    ),
}

# Every queryable source name: tables first, then the virtual ones.
SOURCES = (*FIELDS_BY_SOURCE, *_VIRTUAL_SOURCES)


def source_schema(source: str) -> dict[str, dict]:
    """The full returned-row schema for a source. For a table: its queryable columns
    followed by the calculated columns. For a virtual source: the fields its rows
    carry. Raises ValueError for an unknown source."""
    if source in _VIRTUAL_SOURCES:
        return dict(_VIRTUAL_SOURCES[source].fields)
    if source not in FIELDS_BY_SOURCE:
        raise ValueError(f"unknown source '{source}' (valid: {', '.join(SOURCES)})")
    return {**FIELDS_BY_SOURCE[source], **CALCULATED_BY_SOURCE.get(source, {})}


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


def _calc_filter_schema(col_def: dict) -> dict:
    """The filter a queryable calculated column accepts — its declared `ops` only
    (not the type-generic set), since e.g. relative_path supports eq/prefix, not
    ordering. `prefix` is a string-prefix match; `in` takes an array."""
    value = {"type": col_def["type"]}
    operators = {
        op: ({"type": "array", "items": value, "minItems": 1} if op == "in" else value)
        for op in col_def["ops"]
    }
    return {"type": "object", "properties": operators, "additionalProperties": False, "minProperties": 1}


def _queryable_calculated(source: str) -> dict[str, dict]:
    return {n: c for n, c in CALCULATED_BY_SOURCE.get(source, {}).items() if c.get("queryable")}


def queryable_calculated_columns(source: str) -> dict[str, dict]:
    """Calculated columns of `source` that can be *filtered* (each carrying its `ops`
    and any `requires`), for advertising in prompts/tools. Empty for sources with none."""
    return _queryable_calculated(source)


def exposed_relationships() -> list[tuple[str, str, str, str]]:
    """Foreign-key links among the exposed sources as (source, column, target_source,
    target_column) — introspected from the models. There are no server-side joins, so
    this is the key map a client uses to stitch sources together. Derived (not
    hand-listed), so adding a source/FK updates the prompts' join guidance for free."""
    rels: list[tuple[str, str, str, str]] = []
    for model in _EXPOSED_MODELS:
        deny = _DENY.get(model, set())
        for column in model.__table__.columns:
            if column.name in deny:
                continue
            for fk in column.foreign_keys:
                target = fk.column
                if target.table.name in FIELDS_BY_SOURCE:  # only joins to another exposed source
                    rels.append((model.__tablename__, column.name, target.table.name, target.name))
    return rels


def virtual_source_specs() -> list[tuple[str, tuple, tuple, tuple]]:
    """(name, required_params, optional_params, returned_fields) per virtual source —
    a prompt/tool-facing summary derived from each source's schema + fields."""
    specs = []
    for name, vs in _VIRTUAL_SOURCES.items():
        required = tuple(p for p in vs.schema.get("required", ()) if p != "source")
        optional = tuple(p for p in vs.schema["properties"] if p not in ("source", *required))
        specs.append((name, required, optional, tuple(vs.fields)))
    return specs


def _filters_for(source: str, fields: dict[str, dict]) -> dict[str, dict]:
    props = {column: _filter_schema(schema["type"]) for column, schema in fields.items()}
    for name, col_def in _queryable_calculated(source).items():
        props[name] = _calc_filter_schema(col_def)
    return props


def _requires_rules(source: str) -> list[dict]:
    """if/then rules: filtering a calculated column with a `requires` list forces its
    co-filters (e.g. relative_path requires media_dir_id to pin the root)."""
    return [
        {"if": {"required": [name]}, "then": {"required": [req]}}
        for name, col_def in _queryable_calculated(source).items()
        for req in col_def.get("requires", ())
    ]


def _rows_branch(source: str, fields: dict[str, dict]) -> dict:
    """A row-fetch query: a discriminating `source` const, per-column operator
    filters, `limit`, and nothing else (fail closed)."""
    properties = _filters_for(source, fields)
    properties["source"] = {"const": source}
    properties["limit"] = _LIMIT
    branch = {
        "type": "object",
        "properties": properties,
        "required": ["source"],
        "additionalProperties": False,
    }
    rules = _requires_rules(source)
    if rules:
        branch["allOf"] = rules
    return branch


def _aggregate_branch(source: str, fields: dict[str, dict]) -> dict:
    """An aggregate query: `source` const, `op`, a `field` (required for every op
    but `count`, forbidden for `count`), plus the same operator filters as an
    optional WHERE. `field` aggregates a real column only (calculated columns may be
    filtered but not grouped/measured)."""
    properties = _filters_for(source, fields)
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
            *_requires_rules(source),
        ],
    }


# The published schema is the `oneOf` over every source's rows branch and
# aggregate branch. Validation dispatches to the single matching branch (by source
# + whether `op` is present) so errors are precise rather than oneOf's opaque
# "not valid under any of the given schemas".
_ROWS_VALIDATORS = {s: Draft202012Validator(_rows_branch(s, f)) for s, f in FIELDS_BY_SOURCE.items()}
_AGG_VALIDATORS = {s: Draft202012Validator(_aggregate_branch(s, f)) for s, f in FIELDS_BY_SOURCE.items()}
_VIRTUAL_VALIDATORS = {s: Draft202012Validator(vs.schema) for s, vs in _VIRTUAL_SOURCES.items()}

_QUERY = {"oneOf": [
    *(_rows_branch(s, f) for s, f in FIELDS_BY_SOURCE.items()),
    *(_aggregate_branch(s, f) for s, f in FIELDS_BY_SOURCE.items()),
    *(vs.schema for vs in _VIRTUAL_SOURCES.values()),
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
    if source in _VIRTUAL_SOURCES:
        return _format_errors(_VIRTUAL_VALIDATORS[source], query)
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


def _where_conditions(table, query: dict, source: str) -> list:
    # Calculated columns aren't real table columns; their filters are translated to
    # full_file_path SQL separately (media_dir_repository), so skip them here.
    skip = set(_RESERVED_KEYS) | set(CALCULATED_BY_SOURCE.get(source, {}))
    return [
        _OPERATORS[op](table.c[column], operand)
        for column, filters in query.items()
        if column not in skip
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


def build_query(query: dict, extra_conditions: tuple = ()) -> Select:
    """Translate one table query into a SQLAlchemy Select. A rows query selects the
    source's exposed columns with the per-column operator filters + `limit`; an
    aggregate query (`op` present) builds the count / facet / range. `extra_conditions`
    are pre-built WHERE clauses (e.g. the calculated-column path filters resolved by
    resolve_query) ANDed in. Pure translation — assumes the query passed
    `validate_query`; virtual sources never reach here."""
    source = query["source"]
    table = _MODEL_BY_SOURCE[source].__table__
    conditions = _where_conditions(table, query, source) + list(extra_conditions)
    if "op" in query:
        return _aggregate_select(table, query, conditions)
    stmt = select(*(table.c[name] for name in FIELDS_BY_SOURCE[source]))
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
    source = query["source"]
    if source in _VIRTUAL_SOURCES:
        return _VIRTUAL_SOURCES[source].resolve(session, query)
    # Calculated-column filters (media_dir_id / relative_path) need the media-dir
    # registry, so they're translated here (with the session) and fed into build_query.
    extra = tuple(media_dir_repository.media_item_path_conditions(session, query)) if source == "media_items" else ()
    stmt = build_query(query, extra)
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
