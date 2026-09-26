"""Suggest location names from photos already named nearby -- the offline half of
the Locations page's recommendations. Nothing here goes online: the assistant
profile never looks place names up with a web service (ai-assistant.md →
Network access)."""
import math
from typing import Annotated, Any, Optional

from sqlalchemy.orm import Session

from yaffo.background_tasks.automation_config import AUTOMATION_CONFIG
from yaffo.db.models import AUTOMATION_HANDLER_ASSIGN_LOCATION_NAME
from yaffo.db.repositories import automation_repository, media_repository
from yaffo.utils.geo import haversine_meters

MAX_ITEMS = 500
_KM_PER_DEGREE = 111.32


def default_radius_km(session: Session) -> float:
    """The assign-location-name automation's nearby radius, as the Locations page uses it."""
    field = next(f for f in AUTOMATION_CONFIG[AUTOMATION_HANDLER_ASSIGN_LOCATION_NAME] if f.key == "nearby_radius")
    automation = automation_repository.get_by_handler(session, AUTOMATION_HANDLER_ASSIGN_LOCATION_NAME)
    configured = (automation.config or {}).get("nearby_radius_kilometers") if automation else None
    return float(configured if configured is not None else field.default)


def suggest_location_names(
    session: Session, media_item_ids: list[int], radius_km: Optional[float] = None,
) -> Annotated[list[dict], "Per photo with GPS: {id, location_name, distance_km}; location_name is None when nothing named is near."]:
    """For each photo with GPS coordinates (at most 500), the location name of the
    closest already-named photo within `radius_km` (default: the Locations page's
    nearby radius). Photos without coordinates are left out."""
    if not isinstance(media_item_ids, list) or len(media_item_ids) > MAX_ITEMS:
        raise ValueError(f"media_item_ids must be a list of at most {MAX_ITEMS} ids")
    if radius_km is None:
        radius_km = default_radius_km(session)
    if isinstance(radius_km, bool) or not isinstance(radius_km, (int, float)) or not 0 < radius_km <= 1000:
        raise ValueError("radius_km must be a number from 0 to 1000")
    items = media_repository.get_media_items_with_gps(session, media_item_ids)
    if not items:
        return []
    lat_pad = radius_km / _KM_PER_DEGREE
    widest = max(abs(item.latitude) for item in items) + lat_pad
    lon_pad = min(180.0, radius_km / (_KM_PER_DEGREE * max(math.cos(math.radians(min(widest, 89.0))), 0.01)))
    candidates = media_repository.named_coordinates_in_box(
        session,
        min(item.latitude for item in items) - lat_pad, max(item.latitude for item in items) + lat_pad,
        min(item.longitude for item in items) - lon_pad, max(item.longitude for item in items) + lon_pad,
    )
    suggestions = []
    for item in sorted(items, key=lambda i: media_item_ids.index(i.id)):
        best: tuple[float, Optional[str]] = (radius_km, None)
        for candidate_id, lat, lon, name in candidates:
            if candidate_id == item.id:
                continue
            distance = haversine_meters(item.latitude, item.longitude, lat, lon) / 1000.0
            if distance <= best[0]:
                best = (distance, name)
        suggestions.append({"id": item.id, "location_name": best[1],
                            "distance_km": round(best[0], 3) if best[1] else None})
    return suggestions


def summarize_suggest_location_names(args: list[Any], session: Session) -> str:
    count = len(args[0]) if args and isinstance(args[0], list) else 0
    return f"Suggest location names for {count} photo(s) from named photos nearby"
