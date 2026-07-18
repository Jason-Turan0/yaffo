from pathlib import Path
from urllib.parse import urlencode

import pydash as py_
import requests
from flask import Flask, flash, jsonify, render_template, request, url_for
from flask_babel import format_date as babel_format_date
from flask_babel import gettext
from sqlalchemy import func
from sqlalchemy.orm import joinedload

from yaffo.db import db
from yaffo.db.models import (
    ApplicationSettings,
    Face,
    MediaItem,
    MediaLabel,
    Tag,
)
from yaffo.db.repositories.media_dir_repository import get_media_dirs
from yaffo.db.repositories.media_filter_repository import apply_media_filters
from yaffo.routes import filter_config
from yaffo.routes.filter_panel import build_filters_context, to_media_filters, to_query_params
from yaffo.utils.context import context
from yaffo.utils.photo_dates import parse_date_taken

LIBRARY_VIEWS = ("grid", "timeline")
LIBRARY_VIEW_SETTING = "library_view"


def _resolve_library_view(session) -> str:
    """The view to render: the URL's (persisted as the new preference when it
    changes) or the saved preference, defaulting to grid. The preference lives in
    ApplicationSettings like the theme and distance-unit choices."""
    setting = session.query(ApplicationSettings).filter_by(name=LIBRARY_VIEW_SETTING).first()
    saved = setting.value if setting and setting.value in LIBRARY_VIEWS else "grid"
    requested = request.args.get("view")
    if requested not in LIBRARY_VIEWS:
        return saved
    if requested != saved:
        if setting is None:
            session.add(ApplicationSettings(name=LIBRARY_VIEW_SETTING, type="string", value=requested))
        else:
            setting.value = requested
        session.commit()
    return requested


# "No item precedes this batch" — distinct from None, which means the preceding
# item is undated (the undated tail spans batches too and must merge like a day).
_NO_PREVIOUS_ITEM = object()


def _timeline_groups(media_items: list, previous_day=_NO_PREVIOUS_ITEM) -> list[dict]:
    """The batch's items bucketed by calendar day, in the batch's (date desc) order.
    Each group carries pre-formatted, locale-aware labels; a group opens a new
    month when the month changes, which the template renders as a heavier divider.
    Undated items collect in one trailing group.

    `previous_day` is the day of the item just before this batch (infinite-scroll
    fragments continue mid-library; None = that item is undated): it seeds the
    month-divider logic, and a first group continuing that same day — or
    continuing the undated tail — is marked `continuation` so the template
    suppresses its header and the client merges it into the section above."""
    groups: list[dict] = []
    for media_item in media_items:
        taken = parse_date_taken(media_item.date_taken)
        day = taken.date() if taken else None
        if groups and groups[-1]["date"] == day:
            groups[-1]["media_items"].append(media_item)
            continue
        if groups:
            previous = groups[-1]["date"]
        else:
            previous = None if previous_day is _NO_PREVIOUS_ITEM else previous_day
        same_month = (day is not None and previous is not None
                      and (day.year, day.month) == (previous.year, previous.month))
        groups.append({
            "date": day,
            "day_label": babel_format_date(day, format="full") if day else gettext("Unknown date"),
            "month_label": babel_format_date(day, "MMMM y") if day else None,
            "month_start": not same_month,
            "continuation": not groups and previous_day is not _NO_PREVIOUS_ITEM and day == previous_day,
            "media_items": [media_item],
        })
    return groups


# Cap on rendered year labels so a century-spanning library still reads.
MAX_SCRUBBER_YEAR_LABELS = 20


def _timeline_index(filters: dict, page_size: int, base_params: dict) -> tuple[list[dict], list[dict], list[dict]]:
    """The scrubber's data, built from per-month counts of the whole *filtered*
    library (newest first). The rail axis is TIME (calendar months, newest at the
    top), so year labels space evenly instead of bunching in sparse years; each
    month's count renders as a horizontal density bar at its band. Every month
    also carries its item offset — offset // page_size is the page where it
    starts, because the gallery orders by date desc. Returns (month index for
    the drag JS, density bars, per-year marks for the no-JS fallback links).
    Undated items are absent: they sort after every dated one."""
    # Group on the "YYYY-MM" prefix of date_taken — the SAME value the gallery
    # orders by — never the year/month columns. Any disagreement between the two
    # (rows with a year but no date_taken sort into the undated tail, not their
    # year slot) would drift every older month's offset and land jumps on the
    # wrong page.
    month_prefix = func.substr(MediaItem.date_taken, 1, 7)
    raw_rows = (
        apply_media_filters(
            db.session,
            db.session.query(month_prefix, func.count(MediaItem.id)),
            to_media_filters(filters),
        )
        .filter(MediaItem.date_taken.isnot(None))
        .group_by(month_prefix)
        .order_by(month_prefix.desc())
        .all()
    )
    rows = [
        (int(prefix[:4]), int(prefix[5:7]), count)
        for prefix, count in raw_rows
        if prefix and len(prefix) >= 7 and prefix[:4].isdigit() and prefix[5:7].isdigit()
    ]
    if not rows:
        return [], [], []

    def month_key(year: int, month: int) -> int:
        return year * 12 + month

    newest_key = month_key(rows[0][0], rows[0][1])
    oldest_key = month_key(rows[-1][0], rows[-1][1])
    total_months = newest_key - oldest_key + 1
    max_count = max(count for _y, _m, count in rows)

    months: list[dict] = []
    bars: list[dict] = []
    offset = 0
    for year, month, count in rows:
        months.append({"year": year, "month": month, "count": count, "offset": offset})
        bars.append({
            "top": round((newest_key - month_key(year, month)) / total_months * 100, 3),
            "height": round(1 / total_months * 100, 3),
            # Never thinner than 15%: a one-photo month must still leave a visible tick.
            "width": round(15 + 85 * count / max_count),
        })
        offset += count

    # One mark per calendar year in range, at the top of that year's span; the
    # link lands on the page of the newest photo at or before that point.
    years = list(range(rows[0][0], rows[-1][0] - 1, -1))
    step = -(-len(years) // MAX_SCRUBBER_YEAR_LABELS)  # ceil division
    year_marks: list[dict] = []
    for year in years[::step]:
        top_key = min(newest_key, month_key(year, 12))
        entry = next((m for m in months if month_key(m["year"], m["month"]) <= top_key), months[-1])
        params = {**base_params, "view": "timeline", "page": entry["offset"] // page_size + 1}
        # The month's first photo lands mid-page; the #month anchor scrolls to it.
        anchor = f"month-{entry['year']:04d}-{entry['month']:02d}"
        year_marks.append({
            "year": year,
            "percent": round((newest_key - top_key) / total_months * 100, 2),
            "url": f"{url_for('index')}?{urlencode(params, doseq=True)}#{anchor}",
        })
    return months, bars, year_marks


@context("yaffo-gallery")
def init_home_routes(app: Flask):
    @app.route("/", methods=["GET"])
    def index():
        # Parse filter parameters + build the sidebar's option lists (shared with
        # the locations page, which filters client-side from the same panel).
        filters = build_filters_context(db.session, request.args)
        page = request.args.get("page", default=1, type=int)
        page_size = request.args.get("page-size", type=int)
        filter_page_size = page_size if page_size else 25
        # Build query with eager loading
        query = (
            db.session.query(MediaItem)
            .options(joinedload(MediaItem.faces).joinedload(Face.people))
            .options(joinedload(MediaItem.labels).joinedload(MediaLabel.label))
            .order_by(MediaItem.date_taken.desc())
        )

        # Apply filters (semantics shared with the p2p sharing handler —
        # see media_filter_repository, and with the album add screen via
        # to_media_filters).
        query = apply_media_filters(db.session, query, to_media_filters(filters))

        # Get total count of filtered results
        media_count = query.count()

        # Apply pagination
        offset = (page - 1) * filter_page_size
        media_items = query.limit(filter_page_size).offset(offset).all()


        # Get unique people from photos (for display in cards)
        for media_item in media_items:
            # Create a set of unique people across all faces in the photo
            media_item.people = list({
                person
                for face in media_item.faces
                for person in face.people
            })
            # Split the stored path into name + folder for the hover details
            file_path = Path(media_item.full_file_path) if media_item.full_file_path else None
            media_item.file_name = file_path.name if file_path else ""
            media_item.folder = str(file_path.parent) if file_path else ""

        filters["page_sizes"] = [10, 25, 50, 100, 250]
        filters["page_size"] = filter_page_size

        view = _resolve_library_view(db.session)
        filter_params = to_query_params(filters)
        base_params = {k: v for k, v in filter_params.items() if v not in (None, "", [])}
        timeline_groups: list[dict] = []
        timeline_index: list[dict] = []
        timeline_bars: list[dict] = []
        timeline_year_marks: list[dict] = []
        next_fragment_url = None
        if view == "timeline":
            filter_params = {**filter_params, "view": "timeline"}
            # Only a FRAGMENT continues mid-library: the item just before the
            # batch seeds day/month continuity so a day split across batches
            # doesn't repeat its header. A full render at page > 1 (a scrubber
            # jump) starts fresh — the landing needs its day header and divider.
            is_fragment = request.args.get("fragment") == "sections"
            previous_day = _NO_PREVIOUS_ITEM
            if is_fragment and offset > 0:
                previous_item = query.limit(1).offset(offset - 1).first()
                if previous_item:
                    taken = parse_date_taken(previous_item.date_taken)
                    previous_day = taken.date() if taken else None
            timeline_groups = _timeline_groups(media_items, previous_day)
            if page * filter_page_size < media_count:
                # The infinite-scroll sentinel's target: same filters, next batch.
                params = {**base_params, "view": "timeline", "page-size": filter_page_size,
                          "page": page + 1, "fragment": "sections"}
                next_fragment_url = f"{url_for('index')}?{urlencode(params, doseq=True)}"
            if is_fragment:
                return render_template(
                    "_timeline_sections.html",
                    timeline_groups=timeline_groups,
                    next_fragment_url=next_fragment_url,
                )
            timeline_index, timeline_bars, timeline_year_marks = _timeline_index(
                filters, filter_page_size, base_params)
        # The header toggle: same filters, page 1, other view.
        view_urls = {
            option: f"{url_for('index')}?{urlencode({**base_params, 'view': option}, doseq=True)}"
            for option in LIBRARY_VIEWS
        }

        # Surface unavailable media folders (an unplugged external drive makes
        # every photo under it 404) so the gallery explains itself instead of
        # silently showing broken images.
        missing_media_dirs = [str(d) for d in get_media_dirs(db.session) if not d.exists()]
        if missing_media_dirs:
            flash(gettext(
                "Media folders are not available: %(directories)s. "
                "If they are on an external drive, make sure the drive is connected.",
                directories=", ".join(missing_media_dirs),
            ), "warning")

        pagination = {
            "current_page": page,
            "total_items": media_count,
            "page_size": filter_page_size,
            "page_sizes": [10, 25, 50, 100, 250],
        }

        return render_template(
            "index.html",
            media_items=media_items,
            filters=filters,
            filter_params=filter_params,
            view=view,
            view_urls=view_urls,
            timeline_groups=timeline_groups,
            timeline_index=timeline_index,
            timeline_bars=timeline_bars,
            timeline_year_marks=timeline_year_marks,
            next_fragment_url=next_fragment_url,
            media_count=media_count,
            pagination=pagination,
            filter_layout=filter_config.load_layout(db.session),
            filter_default_keys=filter_config.default_keys(),
        )

    @app.route("/api/tag-values", methods=["GET"])
    def get_tag_values():
        """
        API endpoint to get distinct tag values for a given tag name.
        Query params: tag_name
        """
        tag_name = request.args.get("tag_name")
        if not tag_name:
            return jsonify({
                "error": gettext("The tag_name parameter is required"),
                "code": "tag_name_required",
            }), 400

        distinct_values = (
            db.session.query(Tag.tag_value)
            .filter(Tag.tag_name == tag_name)
            .filter(Tag.tag_value.isnot(None))
            .distinct()
            .order_by(Tag.tag_value)
            .all()
        )

        values = [val[0] for val in distinct_values if val[0]]
        return jsonify({"tag_name": tag_name, "values": values})

    @app.route("/api/location-autocomplete", methods=["GET"])
    def location_autocomplete():
        """
        API endpoint for location autocomplete with geocoding.
        Combines results from:
        1. Existing photo locations in database
        2. OpenStreetMap Nominatim geocoding
        Query params: q (search query)
        """
        query = request.args.get("q", "").strip()
        if not query or len(query) < 2:
            return jsonify({"results": []})

        results = []

        db_locations = (
            db.session.query(MediaItem.location_name, MediaItem.latitude, MediaItem.longitude)
            .filter(MediaItem.location_name.isnot(None))
            .filter(MediaItem.location_name.ilike(f"%{query}%"))
            .limit(5)
            .all()
        )

        for photos_by_name in py_.group_by(
            db_locations,
            lambda media_item: media_item.location_name,
        ).values():
            lat = py_.sum_by(
                photos_by_name,
                lambda media_item: media_item.latitude,
            ) / len(photos_by_name)
            lon = py_.sum_by(
                photos_by_name,
                lambda media_item: media_item.longitude,
            ) / len(photos_by_name)
            if lat is not None and lon is not None:
                results.append({
                    "name": photos_by_name[0].location_name,
                    "lat": lat,
                    "lon": lon,
                    "source": "photos"
                })

        try:
            osm_response = requests.get(
                "https://nominatim.openstreetmap.org/search",
                params={
                    "q": query,
                    "format": "json",
                    "limit": 5
                },
                headers={
                    "User-Agent": "PhotoOrganizer/1.0"
                },
                timeout=3
            )

            if osm_response.status_code == 200:
                osm_data = osm_response.json()
                for item in osm_data:
                    results.append({
                        "name": item.get("display_name"),
                        "lat": float(item.get("lat")),
                        "lon": float(item.get("lon")),
                        "source": "osm"
                    })
        except requests.RequestException:
            pass

        return jsonify({"results": results})
