"""Shared sidebar filter panel context: parses the filter querystring and builds
the option lists the filter partials (templates/filters/_*.html) render from.

Two pages share this panel with different filtering modes: the home gallery
feeds the parsed selections into its SQL query (server-side filtering), while
the locations map renders the same panel but filters its already-loaded markers
in the browser (static/filters/client_filter.js) — that JS mirrors the home
route's filter semantics, so changes to either side must be kept in step.
"""
from flask_babel import gettext
from sqlalchemy import func
from sqlalchemy.orm import Session
from werkzeug.datastructures import MultiDict

from yaffo.common import SHAPES
from yaffo.db.models import (
    MEDIA_TYPE_PHOTO,
    MEDIA_TYPE_VIDEO,
    ClassificationLabel,
    MediaItem,
    Person,
    Tag,
)
from yaffo.db.repositories.media_repository import get_distinct_months, get_distinct_years
from yaffo.distance_units import (
    DISTANCE_UNIT_KILOMETERS,
    distance_to_kilometers,
    get_saved_distance_unit,
)


def gender_options() -> list[dict]:
    return [
        {'name': gettext("Male"), 'value': 1},
        {'name': gettext("Female"), 'value': 0},
    ]


def filter_selections(session: Session, args: MultiDict) -> dict:
    """Just the `selected_*` half of the filters context: the selections
    parsed from the querystring (empty when no args). Shared with the p2p
    remote gallery, whose *option lists* come from a peer's facets instead
    of the local DB."""
    path = args.get("path", type=str)
    path = path.strip() if path else None
    device = args.get("device", type=str)
    device = device.strip() if device else None
    media_type = args.get("media-type", type=str)
    if media_type not in (MEDIA_TYPE_PHOTO, MEDIA_TYPE_VIDEO):
        media_type = None
    shape = args.get("shape", type=str)
    if shape not in SHAPES:
        shape = None
    distance_unit = get_saved_distance_unit(session)

    return {
        'selected_path': path,
        'selected_person_ids': args.getlist("person", type=int),
        'selected_person_match_type': args.get("person-match-type", default='any', type=str),
        'selected_label_ids': args.getlist("labels", type=int),
        'selected_labels_match_type': args.get("labels-match-type", default='any', type=str),
        'selected_tag_name': args.get("tag-name", type=str),
        'selected_tag_value': args.get("tag-value", type=str),
        'selected_location_names': args.getlist("location", type=str),
        'selected_location_match_type': args.get("location-match-type", default='any', type=str),
        'selected_unnamed': args.get("unnamed", type=int),
        'selected_proximity_lat': args.get("proximity-lat", type=float),
        'selected_proximity_lon': args.get("proximity-lon", type=float),
        'selected_proximity_distance': args.get("proximity-distance", type=float),
        'selected_distance_unit': distance_unit,
        'selected_distance_unit_label': gettext("Kilometers") if distance_unit == DISTANCE_UNIT_KILOMETERS else gettext("Miles"),
        'selected_proximity_location': args.get("proximity-location", type=str),
        'selected_year': args.get("year", type=int),
        'selected_month': args.get("month", type=int),
        'selected_device': device,
        'selected_favorite': args.get("favorite", type=int),
        'selected_media_type': media_type,
        'selected_shape': shape,
        'selected_gender': args.get("gender", type=int),
    }


def build_filters_context(session: Session, args: MultiDict) -> dict:
    """The `filters` template context: available options for every filter plus the
    selections parsed from the querystring (empty selections when no args)."""
    distinct_tag_names = (
        session.query(Tag.tag_name)
        .distinct()
        .order_by(Tag.tag_name)
        .all()
    )
    tag_names_list = [tag[0] for tag in distinct_tag_names if tag[0]]

    distinct_locations = (
        session.query(MediaItem.location_name)
        .filter(MediaItem.location_name.isnot(None))
        .distinct()
        .order_by(MediaItem.location_name)
        .all()
    )
    location_names_list = [loc[0] for loc in distinct_locations if loc[0]]

    distinct_devices = (
        session.query(MediaItem.device)
        .filter(MediaItem.device.isnot(None))
        .filter(MediaItem.device != "")
        .distinct()
        .order_by(MediaItem.device)
        .all()
    )
    device_list = [d[0] for d in distinct_devices if d[0]]

    labels = (
        session.query(ClassificationLabel)
        .filter(ClassificationLabel.enabled == True)
        .order_by(func.lower(ClassificationLabel.name))
        .all()
    )

    return {
        'people': session.query(Person).order_by(Person.name).all(),
        'years': get_distinct_years(session),
        'months': get_distinct_months(),
        'tag_names': tag_names_list,
        'location_names': location_names_list,
        'devices': device_list,
        'labels': labels,
        'genders': gender_options(),
        **filter_selections(session, args),
    }

def to_media_filters(filters: dict) -> dict:
    """Map the panel's `selected_*` context onto apply_media_filters' selections.

    One mapping, used by every screen that shows the filter panel — the home
    gallery and the album add screen — so they narrow the library identically.
    That matters most for "add all N matching these filters", which re-runs this
    same selection server-side (album_repository.add_matching): the photos added
    are exactly the photos the filters were showing.
    """
    proximity_distance = filters["selected_proximity_distance"]
    return {
        "path": filters["selected_path"],
        "year": filters["selected_year"],
        "month": filters["selected_month"],
        "device": filters["selected_device"],
        "favorite": filters["selected_favorite"],
        "media_type": filters["selected_media_type"],
        "shape": filters["selected_shape"],
        "person_ids": filters["selected_person_ids"],
        "person_match_type": filters["selected_person_match_type"],
        "gender": filters["selected_gender"],
        "label_ids": filters["selected_label_ids"],
        "labels_match_type": filters["selected_labels_match_type"],
        "tag_name": filters["selected_tag_name"],
        "tag_value": filters["selected_tag_value"],
        "location_names": filters["selected_location_names"],
        "location_match_type": filters["selected_location_match_type"],
        "unnamed": filters["selected_unnamed"],
        "proximity_lat": filters["selected_proximity_lat"],
        "proximity_lon": filters["selected_proximity_lon"],
        "proximity_km": (
            distance_to_kilometers(proximity_distance, filters["selected_distance_unit"])
            if proximity_distance
            else None
        ),
    }


def to_query_params(filters: dict) -> dict:
    """The selections as querystring parameters — for pagination links and for
    carrying the current filters into a POST (the add screen's "all matching")."""
    return {
        "path": filters["selected_path"],
        "year": filters["selected_year"],
        "month": filters["selected_month"],
        "device": filters["selected_device"],
        "person": filters["selected_person_ids"],
        "person-match-type": filters["selected_person_match_type"],
        "tag-name": filters["selected_tag_name"],
        "tag-value": filters["selected_tag_value"],
        "location": filters["selected_location_names"],
        "location-match-type": filters["selected_location_match_type"],
        "unnamed": filters["selected_unnamed"],
        "proximity-lat": filters["selected_proximity_lat"],
        "proximity-lon": filters["selected_proximity_lon"],
        "proximity-distance": filters["selected_proximity_distance"],
        "proximity-location": filters["selected_proximity_location"],
        "labels": filters["selected_label_ids"],
        "labels-match-type": filters["selected_labels_match_type"],
        "gender": filters["selected_gender"],
        "favorite": filters["selected_favorite"],
        "media-type": filters["selected_media_type"],
        "shape": filters["selected_shape"],
    }
