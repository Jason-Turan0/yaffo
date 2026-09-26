"""Why an assistant reply hasn't started: its queued run is waiting for a free
background worker (a big index run can keep every worker busy), or the task host
isn't running at all. Read from the queue on each chat poll, so the dialog can say
so instead of only ticking."""
from __future__ import annotations

import math
import time
from collections import Counter
from dataclasses import dataclass
from datetime import timedelta
from typing import Optional

from flask_babel import format_timedelta, gettext, ngettext

from yaffo.site_agents.assistant.tool_providers.diagnostics.health import HEARTBEAT_STALE_SECONDS
from yaffo.taskq.store import Store

RUN_TASK_NAME = "assistant_run_task"
QUEUE_WAITING = "waiting"
QUEUE_HOST_STOPPED = "host_stopped"
# Run times are averaged over this window for the estimate.
DURATION_WINDOW_SECONDS = 6 * 3600


@dataclass(frozen=True)
class RunQueueStatus:
    """The chat poll's `queue`: why the reply hasn't started, as a ready-to-show
    `message`, plus the facts behind it."""
    state: str                    # QUEUE_WAITING or QUEUE_HOST_STOPPED
    ahead: int                    # queued tasks that start first
    workers: int
    busy: int
    busy_with: Optional[str]      # task name most workers are running
    wait_seconds: Optional[int]   # rough time until it starts; None when unknown
    message: str


def _busy_sentence(task_name: Optional[str]) -> str:
    return {
        "index_photo_task": gettext("Waiting for a background worker; they're busy indexing photos."),
        "import_photo_task": gettext("Waiting for a background worker; they're busy importing photos."),
        "find_duplicates_task": gettext("Waiting for a background worker; they're busy looking for duplicates."),
        "export_photo_tag_task": gettext("Waiting for a background worker; they're busy writing tags to photo files."),
    }.get(task_name or "", gettext("Waiting for a free background worker."))


def _estimate(store: Store, running: list[tuple[str, float]], ahead: int, now: float) -> Optional[int]:
    """Seconds until a worker is free for this run: when the running task that
    finishes (ahead + 1)th is expected to end, from recent average run times.
    None when there's too much ahead to say, or no history for what's running."""
    averages = store.average_durations(sorted({name for name, _ in running}), now - DURATION_WINDOW_SECONDS)
    remaining = sorted(
        max(0.0, averages[name] - (now - (started or now)))
        for name, started in running if name in averages
    )
    if ahead >= len(remaining):
        return None
    return max(1, math.ceil(remaining[ahead]))


def run_queue_status(store: Store, conversation_id: int, now: Optional[float] = None) -> Optional[RunQueueStatus]:
    """Why this conversation's reply hasn't started, or None when it isn't waiting
    in the queue (already running, finished, or about to be picked up by a free
    worker)."""
    now = time.time() if now is None else now
    wait = store.queue_wait(RUN_TASK_NAME, [conversation_id])
    if wait is None:
        return None
    heartbeat = store.read_heartbeat()
    busy_with = Counter(name for name, _ in wait.running).most_common(1)
    busy_with = busy_with[0][0] if busy_with else None

    if heartbeat is None or now - heartbeat.beat_at > HEARTBEAT_STALE_SECONDS:
        return RunQueueStatus(
            state=QUEUE_HOST_STOPPED, ahead=wait.ahead, workers=0, busy=0, busy_with=None,
            wait_seconds=None,
            message=gettext("Waiting to start: Yaffo's background worker isn't running."),
        )
    if heartbeat.busy < heartbeat.workers and wait.ahead == 0:
        return None  # a free worker picks it up on the host's next dispatch

    wait_seconds = _estimate(store, wait.running, wait.ahead, now)
    parts = [_busy_sentence(busy_with)]
    if wait.ahead:
        parts.append(ngettext(
            "%(num)d task is ahead of it.", "%(num)d tasks are ahead of it.", wait.ahead))
    if wait_seconds is not None:
        parts.append(gettext(
            "Starts in about %(time)s.",
            time=format_timedelta(timedelta(seconds=wait_seconds), threshold=1.1)))
    return RunQueueStatus(
        state=QUEUE_WAITING, ahead=wait.ahead, workers=heartbeat.workers, busy=heartbeat.busy,
        busy_with=busy_with, wait_seconds=wait_seconds, message=" ".join(parts),
    )
