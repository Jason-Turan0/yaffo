"""Provider-neutral recorded host calls and preview return references."""
from dataclasses import dataclass
import re
from typing import Any, Mapping


@dataclass(frozen=True)
class HostCall:
    name: str
    args: list[Any]


_REFERENCE = re.compile(r"\$ref:(0|[1-9][0-9]*)\Z")


def reference(index: int) -> str:
    return f"$ref:{index}"


def resolve_references(value: Any, results: Mapping[int, Any]) -> Any:
    """Resolve nested references against earlier mutation results only.

    The caller supplies only results from completed earlier steps. Reserved but
    malformed, forward, and missing references fail closed, never become ids.
    """
    if isinstance(value, str) and value.startswith("$ref:"):
        match = _REFERENCE.fullmatch(value)
        if match is None or int(match[1]) not in results:
            raise ValueError(f"Unknown or invalid preview reference: {value}")
        return results[int(match[1])]
    if isinstance(value, list):
        return [resolve_references(item, results) for item in value]
    if isinstance(value, dict):
        return {key: resolve_references(item, results) for key, item in value.items()}
    return value
