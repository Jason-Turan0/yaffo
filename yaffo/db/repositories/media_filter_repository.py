"""Media filter semantics shared by everything that filters MediaItem in SQL:
the home gallery filters the local library, and the p2p sharing handler
applies the same semantics to a grant-scoped query on behalf of a peer (the
grant is applied first — filters can only narrow it). The locations map
mirrors these semantics client-side in static/filters/client_filter.js; keep
all three in step.
"""
from __future__ import annotations

import math

from sqlalchemy import func
from sqlalchemy.orm import Session

from yaffo.common import SHAPE_LANDSCAPE, SHAPE_PORTRAIT, SHAPE_SQUARE
from yaffo.db.models import (
    Face,
    MediaItem,
    MediaLabel,
    Person,
    PersonFace,
    Tag,
)


def calculate_bounding_box(lat: float, lon: float, distance_kilometers: float) -> tuple[float, float, float, float]:
    """
    Calculate bounding box coordinates for a given center point and distance.
    Returns (min_lat, max_lat, min_lon, max_lon)
    """
    lat_degree_kilometers = 111.0
    lon_degree_kilometers = abs(math.cos(math.radians(lat)) * 111.0)

    lat_offset = distance_kilometers / lat_degree_kilometers
    lon_offset = distance_kilometers / lon_degree_kilometers

    min_lat = lat - lat_offset
    max_lat = lat + lat_offset
    min_lon = lon - lon_offset
    max_lon = lon + lon_offset

    return (min_lat, max_lat, min_lon, max_lon)


def apply_media_filters(session: Session, query, selections: dict):
    """Narrow a MediaItem query by the given selections (all keys optional;
    empty/None values are skipped):

    path (str, icontains on the stored path), year/month (int), device (str),
    favorite (truthy), media_type (str), shape (portrait|landscape|square,
    compared against the stored width/height), person_ids (list[int]) +
    person_match_type ('any'|'all'), gender (int), label_ids (list[int]) +
    labels_match_type, tag_name/tag_value (str), location_names (list[str]) +
    location_match_type, unnamed (truthy), proximity_lat/proximity_lon/
    proximity_km (floats — distance already normalized to kilometers).
    """
    path = selections.get("path")
    year = selections.get("year")
    month = selections.get("month")
    device = selections.get("device")
    favorite = selections.get("favorite")
    media_type = selections.get("media_type")
    shape = selections.get("shape")
    person_ids = selections.get("person_ids")
    person_match_type = selections.get("person_match_type")
    gender = selections.get("gender")
    label_ids = selections.get("label_ids")
    labels_match_type = selections.get("labels_match_type")
    tag_name = selections.get("tag_name")
    tag_value = selections.get("tag_value")
    location_names = selections.get("location_names")
    location_match_type = selections.get("location_match_type")
    unnamed = selections.get("unnamed")
    proximity_lat = selections.get("proximity_lat")
    proximity_lon = selections.get("proximity_lon")
    proximity_km = selections.get("proximity_km")

    if path:
        # Partial, case-insensitive match on any part of the stored path
        # (folders or file name); autoescape so %/_ in the term stay literal.
        query = query.filter(MediaItem.full_file_path.icontains(path, autoescape=True))
    if year:
        query = query.filter(MediaItem.year == year)
    if month:
        query = query.filter(MediaItem.month == month)
    if device:
        query = query.filter(MediaItem.device == device)
    if favorite:
        query = query.filter(MediaItem.favorite.is_(True))
    if media_type:
        query = query.filter(MediaItem.media_type == media_type)
    if shape:
        # The dimensions are stored upright, so this is the shape as displayed. Items
        # without dimensions (photos indexed before they were recorded) match no
        # shape: NULL comparisons are false, which is the honest answer — we don't
        # know their shape rather than knowing it isn't this one.
        if shape == SHAPE_PORTRAIT:
            query = query.filter(MediaItem.height > MediaItem.width)
        elif shape == SHAPE_LANDSCAPE:
            query = query.filter(MediaItem.width > MediaItem.height)
        elif shape == SHAPE_SQUARE:
            query = query.filter(MediaItem.width == MediaItem.height,
                                 MediaItem.width.isnot(None))

    if person_ids and person_match_type and len(person_ids) > 0:
        if person_match_type == 'all':
            # AND logic: Photo must contain ALL selected people
            for person_id in person_ids:
                subquery = (
                    session.query(MediaItem.id)
                    .join(MediaItem.faces)
                    .join(Face.person_face)
                    .filter(PersonFace.person_id == person_id)
                )
                query = query.filter(MediaItem.id.in_(subquery))
        else:
            # OR logic: Photo must contain ANY of the selected people
            subquery = (
                session.query(MediaItem.id)
                .join(MediaItem.faces)
                .join(Face.person_face)
                .filter(PersonFace.person_id.in_(person_ids))
                .distinct()
            )
            query = query.filter(MediaItem.id.in_(subquery))

    if gender is not None:
        subquery = (
            session.query(MediaItem.id)
            .join(MediaItem.faces)
            .outerjoin(Face.person_face)
            .outerjoin(PersonFace.person)
            .filter(func.coalesce(Person.gender, Face.gender) == gender)
        )
        query = query.filter(MediaItem.id.in_(subquery))

    if label_ids and labels_match_type and len(label_ids) > 0:
        if labels_match_type == 'all':
            for label_id in label_ids:
                subquery = (
                    session.query(MediaItem.id)
                    .join(MediaItem.labels)
                    .filter(MediaLabel.label_id == label_id)
                )
                query = query.filter(MediaItem.id.in_(subquery))
        else:
            subquery = (
                session.query(MediaItem.id)
                .join(MediaItem.labels)
                .filter(MediaLabel.label_id.in_(label_ids)))
            query = query.filter(MediaItem.id.in_(subquery))

    if tag_name and tag_value:
        # Filter by specific tag name and value
        subquery = (
            session.query(MediaItem.id)
            .join(MediaItem.tags)
            .filter(Tag.tag_name == tag_name)
            .filter(Tag.tag_value == tag_value)
            .distinct()
        )
        query = query.filter(MediaItem.id.in_(subquery))
    elif tag_name:
        # Filter by tag name only (any value)
        subquery = (
            session.query(MediaItem.id)
            .join(MediaItem.tags)
            .filter(Tag.tag_name == tag_name)
            .distinct()
        )
        query = query.filter(MediaItem.id.in_(subquery))

    if location_names and location_match_type and len(location_names) > 0:
        # For locations, 'all' doesn't make sense (a photo can only have one
        # location), so both match types behave as 'any'.
        query = query.filter(MediaItem.location_name.in_(location_names))

    if unnamed:
        # Only photos with no location name; NULL and "" both count as unnamed
        # (the locations map treats a falsy name the same way).
        query = query.filter(func.coalesce(MediaItem.location_name, "") == "")

    if proximity_lat is not None and proximity_lon is not None and proximity_km:
        min_lat, max_lat, min_lon, max_lon = calculate_bounding_box(proximity_lat, proximity_lon, proximity_km)
        query = query.filter(
            MediaItem.latitude.isnot(None),
            MediaItem.longitude.isnot(None),
            MediaItem.latitude >= min_lat,
            MediaItem.latitude <= max_lat,
            MediaItem.longitude >= min_lon,
            MediaItem.longitude <= max_lon
        )

    return query
