"""System automation `geotag_from_neighbors`: when a photo is indexed, give a
GPS-less photo coordinates borrowed from the closest-in-time photo that *does* have
GPS (time-correlation geotagging).

The classic case is a dedicated camera (no GPS chip) shooting alongside a phone (GPS
on every frame) on the same outing: sorted by capture time the two interleave, so a
DSLR frame can take the coordinates of a phone frame a few minutes either side. The
match only happens within a configurable window (`max_minutes`) so coordinates are
never copied across a long gap (a different place). When the matched source already
has a `location_name`, that's copied too (unless the target already has its own).

Candidates (photos that already have GPS) are frozen at the start of the run, so a
just-geotagged photo is never itself used as a source — inferred coordinates can't
chain and drift. Fired by the `photo_indexed` event; the run is recorded as a Job.
"""
import bisect
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from yaffo.background_tasks.automation_config import AUTOMATION_CONFIG, config_value
from yaffo.background_tasks.automation_runs import record_run
from yaffo.background_tasks.config import task_queue
from yaffo.background_tasks.events import EventContext
from yaffo.background_tasks.progress_reporter import ProgressReporter
from yaffo.background_tasks.registry import register_handler
from yaffo.background_tasks.utils import SessionFactory
from yaffo.db.models import Automation, AUTOMATION_HANDLER_GEOTAG_FROM_NEIGHBORS, MediaItem
from yaffo.db.repositories import photos_repository
from yaffo.utils.photo_dates import parse_date_taken

# The handler's lone config field (see automation_config.AUTOMATION_CONFIG).
_MINUTES_FIELD = AUTOMATION_CONFIG[AUTOMATION_HANDLER_GEOTAG_FROM_NEIGHBORS][0]


# A matched GPS source: (latitude, longitude, location_name | None).
Match = tuple[float, float, Optional[str]]

# Photos geotagged before each commit — flush the coordinate updates in chunks so a
# long batch stays durable and the write lock is taken in short bursts.
_FLUSH_SIZE = 200


def _nearest_match(
    target: datetime,
    times: list[datetime],
    values: list[Match],
    max_delta: timedelta,
) -> Optional[Match]:
    """The (lat, lon, location_name) of the candidate closest in time to `target`
    within `max_delta`, or None. `times` is sorted ascending; `values[i]` belongs to
    `times[i]`."""
    pos = bisect.bisect_left(times, target)
    best = None
    best_delta = max_delta
    for j in (pos - 1, pos):
        if 0 <= j < len(times):
            delta = abs(times[j] - target)
            if delta <= best_delta:
                best_delta = delta
                best = values[j]
    return best


def _geotag_from_neighbors(session: Session, progress_reporter: ProgressReporter, photo_ids: list[int], max_minutes: int) -> list[int]:
    """Give each GPS-less photo in `photo_ids` the coordinates (and location name, if
    the source has one) of the closest-in-time GPS-tagged photo within `max_minutes`;
    return the ids actually updated (committed). Candidates are loaded once up front,
    so newly-geotagged photos aren't reused."""
    targets = photos_repository.get_photos_missing_gps(session, photo_ids)
    if not targets:
        progress_reporter.progress_update(1,1,0,0)
        return []

    candidates = []
    for date_str, lat, lon, location_name in photos_repository.get_gps_timestamps(session):
        dt = parse_date_taken(date_str)
        if dt is not None:
            candidates.append((dt, lat, lon, location_name))
    if not candidates:
        progress_reporter.progress_update(1, 1, 0, 0)
        return []
    candidates.sort(key=lambda c: c[0])
    times = [c[0] for c in candidates]
    values: list[Match] = [(c[1], c[2], c[3]) for c in candidates]
    max_delta = timedelta(minutes=max_minutes)

    updated: list[int] = []
    pending: list[int] = []

    def flush():
        if pending:
            session.commit()  # persist the chunk's coordinate updates (dirty ORM photos)
            pending.clear()

    def photo_processor (photo: MediaItem):
        target = parse_date_taken(photo.date_taken)
        if target is None:
            return
        match = _nearest_match(target, times, values, max_delta)
        if match is not None:
            lat, lon, location_name = match
            photo.latitude = lat
            photo.longitude = lon
            if location_name and not (photo.location_name or "").strip():
                photo.location_name = location_name
            updated.append(photo.id)
            pending.append(photo.id)
            if len(pending) >= _FLUSH_SIZE:
                flush()
    progress_reporter.run_with_progress(targets, photo_processor)
    flush()  # remaining tail
    return updated


@task_queue.task()
def geotag_from_neighbors_automation_task(automation_id: int, photo_ids: list[int]):
    """Geotag the GPS-less photos in `photo_ids` from their temporal neighbours.
    Enqueued by the geotag_from_neighbors handler on a photo_indexed event; the time
    window is read live from the automation's config. The run is recorded as a Job."""
    session = SessionFactory()
    try:
        automation = session.get(Automation, automation_id)
        if automation is None:
            return
        max_minutes = int(config_value(automation, _MINUTES_FIELD))

        def work(progress_reporter: ProgressReporter) -> str:
            updated = _geotag_from_neighbors(session, progress_reporter, photo_ids, max_minutes)
            return f"geotagged {len(updated)}/{len(photo_ids)} photo(s) within {max_minutes} min"

        record_run(session, automation, work)
    finally:
        session.close()
        SessionFactory.remove()


@register_handler(AUTOMATION_HANDLER_GEOTAG_FROM_NEIGHBORS)
def enqueue_geotag_from_neighbors(automation: Automation, context: EventContext | None = None) -> None:
    """Handler for the built-in geotag-from-neighbors automation: enqueue the geotag
    for the photos the triggering event concerns. A schedule trigger (no context, no
    photo subjects) has nothing to act on, so it's a no-op."""
    photo_ids = context.media_ids if context else []
    if photo_ids:
        geotag_from_neighbors_automation_task(automation.id, photo_ids)
