"""What the sync engine calls: source and target handlers.

A handler is the provider-specific part of a sync. Built-in adapters (the Folder
source and target) implement these classes in Python; Starlark adapters (Phase 2) are
wrapped by handlers that call their hooks. The engine only ever talks to these
interfaces, so diffing, the ledger, batching, conversion and crash recovery are the
same for both.

Calls return the hook envelope of the adapter contract as plain dicts
({"results"|"records": [...], "state_patch": {...}, "error": None | {code, ...}}), and
the engine validates them the same way whatever produced them.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from yaffo.sync.convert import Accepts

# Hook error codes (the adapter contract's error table).
ERROR_RATE_LIMITED = "rate_limited"
ERROR_RETRY_LATER = "retry_later"
ERROR_AUTH = "auth"
ERROR_NOT_FOUND = "not_found"
ERROR_REJECTED = "rejected"
ERROR_FATAL = "fatal"
ERROR_CURSOR_INVALID = "cursor_invalid"
ERROR_CODES = (ERROR_RATE_LIMITED, ERROR_RETRY_LATER, ERROR_AUTH, ERROR_NOT_FOUND,
               ERROR_REJECTED, ERROR_FATAL, ERROR_CURSOR_INVALID)

# Built-in only: a remove refused because the copy changed since Yaffo wrote it.
ERROR_CHANGED = "changed"

TARGETS_ONE = "one"
TARGETS_MANY = "many"

DEFAULT_MAX_BATCH = 50
DEFAULT_MAX_BATCH_BYTES = 200_000_000

PRESENT = "present"
MISSING = "missing"


@dataclass(frozen=True)
class TargetCapabilities:
    max_batch: int = DEFAULT_MAX_BATCH
    max_batch_bytes: int = DEFAULT_MAX_BATCH_BYTES
    remove: bool = False
    update: bool = False
    reconcile: bool = False
    targets: str = TARGETS_ONE
    accepts: Accepts | None = None


@dataclass(frozen=True)
class HookContext:
    """`ctx` for a hook call (the contract's ctx, plus host-only fields the
    built-ins need)."""
    options: dict[str, Any]
    state: dict[str, Any]
    run_id: str | None
    pass_kind: str
    preview: bool = False
    automation_id: int | None = None
    scratch_dir: Path | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_contract(self) -> dict[str, Any]:
        return {"options": self.options, "state": self.state,
                "run": {"preview": self.preview, "run_id": self.run_id, "pass": self.pass_kind}}


class TargetHandler:
    """A target's provider-specific operations. Batch items carry `key` (the ledger
    row id as a string); every result must name a key from its batch."""

    def capabilities(self, options: dict[str, Any]) -> TargetCapabilities:
        raise NotImplementedError

    def destination_problem(self, options: dict[str, Any]) -> str | None:
        """A pause code when the destination can't be used right now (an unplugged
        drive), checked once at run start. None when it's fine."""
        return None

    def describe_destination(self, options: dict[str, Any]) -> str:
        """Where items go, for the consent sentence and the detail page."""
        raise NotImplementedError

    def push(self, batch: list[dict[str, Any]], ctx: HookContext) -> dict[str, Any]:
        raise NotImplementedError

    def remove(self, items: list[dict[str, Any]], ctx: HookContext) -> dict[str, Any]:
        raise NotImplementedError

    def update(self, items: list[dict[str, Any]], ctx: HookContext) -> dict[str, Any]:
        raise NotImplementedError

    def reconcile(self, items: list[dict[str, Any]], ctx: HookContext) -> dict[str, str]:
        """Which of these sent or pending ledger rows the remote still holds:
        {key: PRESENT | MISSING}. Rows not in the result are left as they are."""
        raise NotImplementedError


class SourceHandler:
    """A source's provider-specific operations."""

    def source_problem(self, options: dict[str, Any]) -> str | None:
        """An outcome code when the source can't be read right now (a card that
        isn't connected). The run ends normally with that outcome; it isn't a pause."""
        return None

    def describe_source(self, options: dict[str, Any]) -> str:
        raise NotImplementedError

    def list_page(self, cursor: str | None, ctx: HookContext) -> dict[str, Any]:
        raise NotImplementedError

    def fetch(self, record: dict[str, Any], destination: Path, ctx: HookContext) -> None:
        """Write the record's bytes to `destination` (a temporary path)."""
        raise NotImplementedError
