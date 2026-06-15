"""The host API exposed to sandboxed automation Starlark.

Declared once in HOST_API and read in two places that must never diverge:
1. build_host_functions -- the live callables a script can invoke, bound to a
   session (the only way a sandboxed script reaches host state), and
2. render_host_api -- the agent-facing docs embedded in the automation system
   prompt, so the model writes against the real, current surface.

Add a capability = add one HostFunction entry; both the runtime and the docs pick
it up.
"""
from dataclasses import dataclass
from typing import Any, Callable

from sqlalchemy.orm import Session

from yaffo.db.repositories.data_query_repository import resolve_query


@dataclass(frozen=True)
class HostFunction:
    """One callable exposed to sandboxed scripts. `impl` takes the session as its
    first argument; the bound callable a script sees drops it. `signature`,
    `description`, `returns`, `example` are the agent docs."""
    name: str
    signature: str
    description: str
    returns: str
    example: str
    impl: Callable[..., Any]


def _data_query(session: Session, query: dict) -> Any:
    return resolve_query(session, query)


HOST_API: tuple[HostFunction, ...] = (
    HostFunction(
        name="data_query",
        signature="data_query(query)",
        description=(
            "Read-only access to the app's data through the validated data_query "
            "contract. `query` is a dict naming a source, with optional per-column "
            'operator filters and a limit, e.g. {"source": "photos", "year": '
            '{"eq": 2024}, "id": {"in": [1, 2, 3]}, "limit": 24}. Operators: eq, ne, '
            "lt, lte, gt, gte, contains, in. You never touch the database directly "
            "-- declare what you want and the server resolves it."
        ),
        returns="A list of row dicts; or a single number/object for count/range queries.",
        example='recent = data_query({"source": "photos", "limit": 10})',
        impl=_data_query,
    ),
)


def _bind(impl: Callable[..., Any], session: Session) -> Callable[..., Any]:
    def call(*args: Any) -> Any:
        return impl(session, *args)
    return call


def build_host_functions(session: Session) -> dict[str, Callable[..., Any]]:
    """The curated host callables for a run, derived from HOST_API and bound to
    `session` so each reads within the caller's transaction. Pass as `functions`
    to run_starlark."""
    return {fn.name: _bind(fn.impl, session) for fn in HOST_API}


def render_host_api() -> str:
    """The host API as agent-facing docs for the automation system prompt -- one
    block per callable. Single source with build_host_functions, so the advertised
    API can't drift from what the sandbox actually provides."""
    blocks: list[str] = []
    for fn in HOST_API:
        blocks.append(
            f"{fn.signature}\n"
            f"  {fn.description}\n"
            f"  Returns: {fn.returns}\n"
            f"  Example: {fn.example}"
        )
    return "\n\n".join(blocks)
