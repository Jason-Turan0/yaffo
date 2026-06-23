"""Print the data_query JSON Schema (and a per-source operator summary) that the
page-builder validates the model's queries against.

Usage:
    python -m scripts.print_data_query_schema          # full schema + summary
    python -m scripts.print_data_query_schema --summary  # summary only
"""
import json
import sys

from yaffo.db.repositories import data_query_repository as dq


def _operators(filter_schema: dict) -> list[str]:
    return list(filter_schema.get("properties", {}))


def print_summary() -> None:
    print("Sources and their filterable columns (operators per column):\n")
    rows_branches = {
        b["properties"]["source"]["const"]: b
        for b in dq.QUERY_SCHEMA["oneOf"]
        if "op" not in b["properties"]  # rows branch, not the aggregate one
    }
    for source, fields in dq.FIELDS_BY_SOURCE.items():
        print(f"  {source}")
        props = rows_branches[source]["properties"]
        for column, schema in fields.items():
            ops = ", ".join(_operators(props[column]))
            print(f"    - {column} ({schema['type']}): {ops}")
        print()
    print(f"  aggregate ops (any source, via 'op'): {', '.join(dq.AGGREGATE_OPS)}")
    print()


def print_schema() -> None:
    with open("temp.json", "w") as file:
        print(json.dumps(dq.DATA_QUERY_SCHEMA, indent=2))
        json.dump(dq.DATA_QUERY_SCHEMA, file, indent=4)


def main() -> None:
    summary_only = "--summary" in sys.argv[1:]
    print_summary()
    if not summary_only:
        print("Full DATA_QUERY_SCHEMA:\n")
        print_schema()


if __name__ == "__main__":
    main()