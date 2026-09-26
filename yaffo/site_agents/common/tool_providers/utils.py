"""Shared tool-provider utilities."""
from __future__ import annotations

_MAX_TOOL_RESULT_CHARS = 15000


def truncate_tool_result(result: str, max_chars: int = _MAX_TOOL_RESULT_CHARS) -> str:
    """Cap a tool result so a large payload can't blow the context window. Trims
    on a line boundary and appends a note about what was dropped."""
    if len(result) <= max_chars:
        return result
    total_lines = result.count("\n") + 1
    truncated = result[:max_chars]
    last_newline = truncated.rfind("\n")
    if last_newline > 0:
        truncated = truncated[:last_newline]
    returned_lines = truncated.count("\n") + 1
    note = (
        f"\n\n[TRUNCATED: showing {returned_lines} of {total_lines} lines "
        f"({len(truncated)} of {len(result)} chars)]"
    )
    return truncated + note