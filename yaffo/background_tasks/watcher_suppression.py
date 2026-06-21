"""Watcher self-write suppression — Mechanism 2 of the automation loop guard.

When yaffo writes a file it also watches (e.g. `export_photo_tag` writing tags back
into a photo's metadata), the OS watcher (`background_tasks/watcher.py`, a separate
process) detects the change and re-indexes it, which emits `photo_indexed` — and that
can re-trigger automations. The in-memory causal chain (Mechanism 1) can't see across
the filesystem + separate-process boundary, so this is the companion guard: the writer
**records its own write**, and the watcher **ignores the event it caused**.

Suppression is keyed on a `(path, signature)` where `signature` is the file's
`size:mtime_ns` right after the write. A genuine *external* edit produces a different
signature and is therefore never suppressed; matching is to the exact bytes yaffo
wrote, not a blanket mute on the path. Entries live in the queue DB (shared across the
worker that writes and the watcher process) and expire after `SUPPRESSION_TTL_SECONDS`.
See docs/automations.md (Loop guard → Mechanism 2).
"""
from __future__ import annotations

import os
from pathlib import Path

from yaffo.background_tasks.config import task_queue
from yaffo.logging_config import get_logger

logger = get_logger(__name__, 'background_tasks')

# How long a recorded self-write stays eligible to suppress its OS event. Must comfortably
# exceed the watcher's settle window (SETTLE_SECONDS) plus queue/worker latency; a stale
# entry can only ever mute a later edit with an *identical* size+mtime_ns, which is
# effectively impossible, so we err generous.
SUPPRESSION_TTL_SECONDS = 120.0


def file_signature(path: str | Path) -> str | None:
    """`"<size>:<mtime_ns>"` for `path`, or None if it can't be stat'd (missing). Both
    the writer and the watcher derive it from the same file, so the comparison is exact
    integer equality."""
    try:
        st = os.stat(path)
    except OSError:
        return None
    return f"{st.st_size}:{st.st_mtime_ns}"


def record_self_write(path: str | Path) -> None:
    """Record that yaffo just wrote `path`, so the watcher skips the resulting OS event.
    No-op if the file vanished before we could stat it (nothing to suppress)."""
    signature = file_signature(path)
    if signature is None:
        return
    task_queue.store.add_suppression(str(Path(path)), signature)


def should_suppress(path: str | Path) -> bool:
    """True when `path`'s current signature matches a recorded self-write (which is then
    consumed). The watcher calls this before re-indexing a settled add."""
    signature = file_signature(path)
    if signature is None:
        return False
    suppressed = task_queue.store.consume_suppression(
        str(Path(path)), signature, SUPPRESSION_TTL_SECONDS
    )
    if suppressed:
        logger.debug(f"watcher: suppressing self-written file (no re-index): {path}")
    return suppressed


def sweep_expired() -> int:
    """Drop suppression entries past their TTL. Called each watcher poll."""
    return task_queue.store.sweep_suppressions(SUPPRESSION_TTL_SECONDS)
