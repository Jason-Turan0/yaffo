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
    ClassificationLabel,
    MediaItem,
    Person,
    Tag,
)
from yaffo.db.repositories.media_repository import get_distinct_months, get_distinct_years
from yaffo.distance_units import DISTANCE_UNIT_KILOMETERS, get_saved_distance_unit
from yaffo.domain.media_filter_params import (
    MEDIA_FILTER_PARAMS,
    filter_query_params,
    media_filter_selections,
    parse_filter_params,
    wire_filters,
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
    of the local DB. The parameters themselves are declared once, in
    domain/media_filter_params.py."""
    distance_unit = get_saved_distance_unit(session)
    return {
        **{f"selected_{key}": value for key, value in parse_filter_params(args).items()},
        'selected_distance_unit': distance_unit,
        'selected_distance_unit_label': gettext("Kilometers") if distance_unit == DISTANCE_UNIT_KILOMETERS else gettext("Miles"),
    }


def _values(filters: dict) -> dict:
    """The `selected_*` context back to plain selection keys."""
    return {param.key: filters[f"selected_{param.key}"] for param in MEDIA_FILTER_PARAMS}


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
        .filter(ClassificationLabel.enabled.is_(True))
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
    return media_filter_selections(_values(filters), filters["selected_distance_unit"])


def to_query_params(filters: dict) -> dict:
    """The selections as querystring parameters — for pagination links and for
    carrying the current filters into a POST (the add screen's "all matching")."""
    return filter_query_params(_values(filters))


def to_wire_filters(filters: dict) -> dict:
    """The selections as a sharing peer's list_files `filters`: only what's set,
    with the proximity radius in kilometers (this device's unit setting)."""
    return wire_filters(_values(filters), filters["selected_distance_unit"])
