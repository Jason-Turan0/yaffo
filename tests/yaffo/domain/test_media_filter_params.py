"""The shared filter-parameter table: one declaration drives parsing, links,
apply_media_filters' selections, and the assistant's tool schema."""
import pytest
from werkzeug.datastructures import MultiDict

from yaffo.domain import media_filter_params as fp

pytestmark = pytest.mark.unit


def test_parses_a_gallery_querystring():
    values = fp.parse_filter_params(MultiDict([
        ("person", "10"), ("person", "11"), ("person-match-type", "all"), ("year", "2019"),
        ("location", "Yellowstone"), ("path", "  trips  "), ("media-type", "gif"), ("month", "13"),
        ("favorite", "1"), ("labels-match-type", "bogus"),
    ]))
    assert values["person_ids"] == [10, 11]
    assert values["person_match_type"] == "all"
    assert values["year"] == 2019
    assert values["location_names"] == ["Yellowstone"]
    assert values["path"] == "trips"
    assert values["media_type"] is None  # not a known media type
    assert values["month"] is None  # out of range
    assert values["favorite"] == 1
    assert values["labels_match_type"] == "any"  # invalid falls back to the default
    assert values["label_ids"] == [] and values["tag_name"] is None


def test_query_params_round_trip():
    args = MultiDict([("person", "10"), ("year", "2019"), ("shape", "portrait"), ("unnamed", "1")])
    values = fp.parse_filter_params(args)
    params = fp.filter_query_params(values)
    assert params["person"] == [10] and params["year"] == 2019 and params["shape"] == "portrait"
    assert fp.parse_filter_params(MultiDict([(k, v) for k, vs in params.items()
                                             for v in (vs if isinstance(vs, list) else [vs]) if v is not None])) == values


def test_media_filter_selections_convert_distance_and_drop_display_only():
    values = fp.parse_filter_params(MultiDict([
        ("proximity-lat", "44.4"), ("proximity-lon", "-110.6"), ("proximity-distance", "10"),
        ("proximity-location", "Old Faithful"),
    ]))
    selections = fp.media_filter_selections(values, "miles")
    assert selections["proximity_km"] == pytest.approx(16.09, rel=1e-3)
    assert "proximity_location" not in selections and "proximity_distance" not in selections


def test_json_values_are_validated_by_the_same_table():
    values, errors = fp.filter_values_from_json({
        "person_ids": [3, "4"], "year": "2019", "favorite": True, "unnamed": False,
        "media_type": "gif", "nonsense": 1,
    })
    given = {k: v for k, v in values.items() if v != fp.default_values()[k]}
    assert given == {"person_ids": [3, 4], "year": 2019, "favorite": 1}
    # Unset filters carry the same defaults the URL parser gives them.
    assert values["person_match_type"] == "any" and values["label_ids"] == [] and values["unnamed"] is None
    assert any("media_type" in e and "photo" in e for e in errors)
    assert any("nonsense" in e for e in errors)


def test_schema_covers_every_parameter():
    schema = fp.filters_json_schema()
    assert set(schema["properties"]) == {p.key for p in fp.MEDIA_FILTER_PARAMS}
    assert schema["properties"]["person_ids"]["type"] == "array"
    assert schema["properties"]["favorite"]["type"] == "boolean"
    assert schema["properties"]["shape"]["enum"] == ["portrait", "landscape", "square"]
