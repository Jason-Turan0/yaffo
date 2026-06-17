"""System automation `assign_location_name`: when a photo is indexed, give it a
`location_name` derived from its GPS coordinates.

Two strategies, tried cheapest-first per photo:
  1. Reuse the location name of the closest already-named photo within the
     configured radius (free, offline, and propagates the user's own naming).
  2. Reverse-geocode the coordinates via OpenStreetMap Nominatim, throttled to
     ~1 request/second per the service's usage policy.
A photo newly named in this run becomes a reuse candidate for the rest of the
batch, so a cluster of fresh photos costs one online lookup, not one each.

Photos without GPS are skipped, and (unless `overwrite_existing`) so are photos
that already have a name. After naming, a `photo_modified` event is emitted for
the updated photos so the export_photo_tag automation can write the name into the
file. Fired by the `photo_indexed` event with the affected photo ids.
"""
import time
from typing import Callable, Optional

from sqlalchemy.orm import Session

from yaffo.background_tasks.automation_config import AUTOMATION_CONFIG, config_value
from yaffo.background_tasks.config import huey
from yaffo.background_tasks.events import EventContext, emit_event
from yaffo.background_tasks.registry import register_handler
from yaffo.background_tasks.utils import SessionFactory
from yaffo.db.models import (
    Automation,
    EVENT_PHOTO_MODIFIED,
    AUTOMATION_HANDLER_ASSIGN_LOCATION_NAME,
)
from yaffo.db.repositories import photos_repository
from yaffo.logging_config import get_logger
from yaffo.utils.geo import haversine_meters
from yaffo.utils.reverse_geocode import reverse_geocode

logger = get_logger(__name__, 'background_tasks')

# The handler's config fields (see automation_config.AUTOMATION_CONFIG), by key.
_FIELDS = {f.key: f for f in AUTOMATION_CONFIG[AUTOMATION_HANDLER_ASSIGN_LOCATION_NAME]}
# Nominatim asks callers to stay at or below 1 request/second.
_MIN_GEOCODE_INTERVAL_S = 1.0

# A coordinate -> name lookup; None when online geocoding is disabled.
Geocoder = Optional[Callable[[float, float], Optional[str]]]


def _nearest_name(
    lat: float, lon: float, candidates: list[tuple[float, float, str]], radius_m: float
) -> Optional[str]:
    """Name of the closest candidate within `radius_m` metres, or None."""
    best_name = None
    best_distance = radius_m
    for clat, clon, name in candidates:
        distance = haversine_meters(lat, lon, clat, clon)
        if distance <= best_distance:
            best_distance = distance
            best_name = name
    return best_name


def _assign_location_names(
    session: Session,
    photo_ids: list[int],
    *,
    reuse_enabled: bool,
    radius_m: float,
    overwrite: bool,
    geocode: Geocoder,
) -> list[int]:
    """Set location_name on the named-able photos in `photo_ids`; return the ids
    actually updated (committed). A photo named here joins the reuse candidates for
    the remaining photos in the batch."""
    photos = photos_repository.get_photos_with_coords(session, photo_ids)
    if not photos:
        return []
    candidates = photos_repository.get_named_coordinates(session) if reuse_enabled else []

    updated: list[int] = []
    for photo in photos:
        if not overwrite and (photo.location_name or "").strip():
            continue
        name = _nearest_name(photo.latitude, photo.longitude, candidates, radius_m) if reuse_enabled else None
        if name is None and geocode is not None:
            name = geocode(photo.latitude, photo.longitude)
        if name:
            photo.location_name = name
            candidates.append((photo.latitude, photo.longitude, name))
            updated.append(photo.id)

    if updated:
        session.commit()
    return updated


def _throttled_geocoder() -> Callable[[float, float], Optional[str]]:
    """A reverse_geocode wrapper that never calls Nominatim faster than the usage
    policy allows."""
    last_call = 0.0

    def geocode(lat: float, lon: float) -> Optional[str]:
        nonlocal last_call
        wait = _MIN_GEOCODE_INTERVAL_S - (time.monotonic() - last_call)
        if wait > 0:
            time.sleep(wait)
        last_call = time.monotonic()
        return reverse_geocode(lat, lon)

    return geocode


@huey.task()
def assign_location_name_automation_task(automation_id: int, photo_ids: list[int]):
    """Assign location names to the given photos. Enqueued by the
    assign_location_name handler on a photo_indexed event; config is read live."""
    session = SessionFactory()
    try:
        automation = session.get(Automation, automation_id)
        if automation is None:
            return
        reuse_enabled = bool(config_value(automation, _FIELDS["reuse_nearby_enabled"]))
        radius_m = float(config_value(automation, _FIELDS["nearby_radius_meters"]))
        overwrite = bool(config_value(automation, _FIELDS["overwrite_existing"]))
        geocode = _throttled_geocoder() if bool(config_value(automation, _FIELDS["reverse_geocode_enabled"])) else None

        updated = _assign_location_names(
            session,
            photo_ids,
            reuse_enabled=reuse_enabled,
            radius_m=radius_m,
            overwrite=overwrite,
            geocode=geocode,
        )
        logger.info(
            f"assign_location_name: named {len(updated)}/{len(photo_ids)} photo(s) "
            f"(reuse={reuse_enabled} radius={radius_m}m geocode={geocode is not None})"
        )
        if updated:
            # Let export_photo_tag (photo_modified) write the new name into the file.
            emit_event(EVENT_PHOTO_MODIFIED, {"photo_ids": updated})
    finally:
        session.close()
        SessionFactory.remove()


@register_handler(AUTOMATION_HANDLER_ASSIGN_LOCATION_NAME)
def enqueue_assign_location_name(automation: Automation, context: EventContext | None = None) -> None:
    """Handler for the built-in assign-location-name automation: enqueue the naming
    for the photos the triggering event concerns. A schedule trigger (no context,
    no photo subjects) has nothing to act on, so it's a no-op."""
    photo_ids = context.photo_ids if context else []
    if photo_ids:
        assign_location_name_automation_task(automation.id, photo_ids)
