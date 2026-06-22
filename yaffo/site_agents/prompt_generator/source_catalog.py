"""Shared renderings of the data-query source catalog for the model-facing prompts.

Derived from data_query_repository so the page-builder and automation prompts both
describe the same queryable surface (calculated filter columns + virtual sources) and
can't drift from the resolver. Table-column formatting differs between the two prompts,
so that stays in each; the calculated-column and virtual-source lines are identical and
live here.
"""
from __future__ import annotations

from yaffo.db.repositories.data_query_repository import (
    exposed_relationships,
    queryable_calculated_columns,
    virtual_source_specs,
)


def relationship_summary() -> str:
    """The foreign-key join map both prompts share, e.g.
    `tags.media_item_id -> media_items.id, people_face.face_id -> faces.id, …` — derived from the
    models so the page-builder and automation prompts can't describe it differently."""
    return ", ".join(
        f"{source}.{column} -> {target}.{target_column}"
        for source, column, target, target_column in exposed_relationships()
    )


def calculated_filter_lines(sources) -> list[str]:
    """One line per source that has queryable calculated columns, e.g.
    `photos also: media_dir_id {eq|in}, relative_path {eq|prefix} (needs media_dir_id)`."""
    lines = []
    for source in sources:
        cols = queryable_calculated_columns(source)
        if not cols:
            continue
        bits = []
        for name, col in cols.items():
            ops = "|".join(col["ops"])
            needs = f" (needs {', '.join(col['requires'])})" if col.get("requires") else ""
            bits.append(f"{name} {{{ops}}}{needs}")
        lines.append(f"{source} also: " + ", ".join(bits))
    return lines


def virtual_source_lines() -> list[str]:
    """One line per virtual (non-table) source, e.g.
    `folders(media_dir_id (required), path) -> name, photo_count`."""
    lines = []
    for name, required, optional, returns in virtual_source_specs():
        params = ", ".join([*(f"{p} (required)" for p in required), *optional]) or "no params"
        lines.append(f"{name}({params}) -> {', '.join(returns)}")
    return lines
