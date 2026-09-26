"""The shared filter-parameter table: one declaration drives parsing, links,
apply_media_filters' selections, and the assistant's tool schema."""
import json
from pathlib import Path

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


def test_wire_filters_send_only_whats_set_by_wire_name():
    values = fp.parse_filter_params(MultiDict([
        ("person", "7"), ("person-match-type", "all"), ("labels", "3"), ("location", "Lisbon"),
        ("favorite", "1"), ("shape", "portrait"), ("gender", "0"),
        ("proximity-lat", "38.7"), ("proximity-lon", "-9.1"), ("proximity-distance", "10"),
        ("proximity-location", "Lisbon"),
    ]))
    payload = fp.wire_filters(values, "mi")
    assert payload == {
        "people": [7], "person_match_type": "all", "labels": [3], "locations": ["Lisbon"],
        "favorite": True, "shape": "portrait", "gender": 0,
        "proximity_lat": 38.7, "proximity_lon": -9.1, "proximity_km": pytest.approx(16.09344),
    }
    # A default match type isn't sent; an incomplete proximity search isn't either.
    partial = fp.wire_filters(fp.parse_filter_params(MultiDict([("proximity-lat", "38.7")])), "km")
    assert partial == {}


def test_every_wire_request_the_sender_builds_passes_the_receivers_validation():
    # One value for every parameter, set in the URL as the filter panel would.
    url_values = {
        "path": "trip", "year": "2020", "month": "5", "device": "X", "person_ids": "1", "gender": "1",
        "label_ids": "2", "tag_name": "t", "tag_value": "v", "location_names": "L", "unnamed": "1",
        "favorite": "1", "media_type": "video", "shape": "square", "proximity_lat": "1",
        "proximity_lon": "2", "proximity_distance": "3",
    }
    param_by_key = {p.key: p.param for p in fp.MEDIA_FILTER_PARAMS}
    values = fp.parse_filter_params(MultiDict([(param_by_key[k], v) for k, v in url_values.items()]))

    payload = fp.wire_filters(values, "km")

    assert fp.validate_wire_filters(payload) is None
    selections = fp.selections_from_wire(payload)
    assert selections["person_ids"] == [1] and selections["shape"] == "square" and selections["proximity_km"] == 3
    assert selections["person_match_type"] == "any"  # filled in on the receiving side


def test_wire_validation():
    assert fp.validate_wire_filters([]) == "filters must be an object"
    assert fp.validate_wire_filters({"person": 1}) == "unknown filters: person"
    assert fp.validate_wire_filters({"month": 13}) == "invalid value for filter 'month'"
    assert fp.validate_wire_filters({"people": [True]}) == "invalid value for filter 'people'"
    assert fp.validate_wire_filters({"proximity_km": "5"}) == "invalid value for filter 'proximity_km'"
    assert fp.validate_wire_filters({"favorite": True, "year": 2020}) is None


FIXTURE = Path(__file__).resolve().parents[3] / "tests_js" / "fixtures" / "media_filter_config.json"


def test_js_fixture_matches_the_table():
    """tests_js reads the filter table from this fixture, so the browser's form
    reading is tested against the real table. Regenerate after changing the table:
    python -c "import json; from yaffo.domain.media_filter_params import client_filter_config as c;
    open('tests_js/fixtures/media_filter_config.json','w').write(json.dumps(c(), indent=2) + '\\n')"
    """
    assert json.loads(FIXTURE.read_text()) == json.loads(json.dumps(fp.client_filter_config()))


@pytest.mark.parametrize("raw,expected", [
    ("1", 1), ("true", 1), ("TRUE", 1), ("on", 1), ("yes", 1),
    ("0", None), ("false", None), ("no", None), ("", None), ("junk", None),
])
def test_on_off_filters_accept_common_spellings_in_the_url(raw, expected):
    values = fp.parse_filter_params(MultiDict([("favorite", raw), ("unnamed", raw)]))
    assert values["favorite"] == expected and values["unnamed"] == expected
