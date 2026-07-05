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

from yaffo.db.models import (
    MEDIA_TYPE_PHOTO,
    MEDIA_TYPE_VIDEO,
    ClassificationLabel,
    MediaItem,
    Person,
    Tag,
)
from yaffo.db.repositories.media_repository import get_distinct_months, get_distinct_years
from yaffo.distance_units import DISTANCE_UNIT_KILOMETERS, get_saved_distance_unit


def build_filters_context(session: Session, args: MultiDict) -> dict:
    """The `filters` template context: available options for every filter plus the
    selections parsed from the querystring (empty selections when no args)."""
    path = args.get("path", type=str)
    path = path.strip() if path else None
    device = args.get("device", type=str)
    device = device.strip() if device else None
    media_type = args.get("media-type", type=str)
    if media_type not in (MEDIA_TYPE_PHOTO, MEDIA_TYPE_VIDEO):
        media_type = None
    distance_unit = get_saved_distance_unit(session)

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
        'genders': [
            {'name': gettext("Male"), 'value': 1},
            {'name': gettext("Female"), 'value': 0},
        ],
        'selected_path': path,
        'selected_person_ids': args.getlist("person", type=int),
        'selected_person_match_type': args.get("person-match-type", default='any', type=str),
        'selected_label_ids': args.getlist("labels", type=int),
        'selected_labels_match_type': args.get("labels-match-type", default='any', type=str),
        'selected_tag_name': args.get("tag-name", type=str),
        'selected_tag_value': args.get("tag-value", type=str),
        'selected_location_names': args.getlist("location", type=str),
        'selected_location_match_type': args.get("location-match-type", default='any', type=str),
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
        'selected_gender': args.get("gender", type=int),
    }