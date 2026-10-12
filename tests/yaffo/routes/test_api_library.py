"""GET /api/tags/<name>/values and GET /api/locations: the filter panel's lookups."""
import pytest
import requests

from yaffo.db import db
from yaffo.db.models import MediaItem, Tag

pytestmark = pytest.mark.unit


@pytest.fixture
def tagged(app):
    item = MediaItem(full_file_path="/lib/a.jpg", location_name="Lisbon", latitude=38.7, longitude=-9.1)
    item.tags = [
        Tag(tag_name="Keywords", tag_value="beach"),
        Tag(tag_name="Keywords", tag_value="aunt"),
        Tag(tag_name="Camera/Lens", tag_value="50mm"),
        Tag(tag_name="Keywords", tag_value=None),
    ]
    db.session.add(item)
    db.session.commit()


def test_tag_values_are_distinct_and_sorted(client, tagged):
    response = client.get("/api/tags/Keywords/values")
    assert response.status_code == 200
    assert response.get_json() == {"tag": "Keywords", "values": ["aunt", "beach"]}


def test_tag_name_with_a_slash_still_matches(client, tagged):
    response = client.get("/api/tags/Camera%2FLens/values")
    assert response.get_json() == {"tag": "Camera/Lens", "values": ["50mm"]}


def test_unknown_tag_has_no_values(client, tagged):
    assert client.get("/api/tags/Nope/values").get_json() == {"tag": "Nope", "values": []}


def test_locations_combine_library_places_and_geocoding(client, tagged, monkeypatch):
    def offline(*_args, **_kwargs):
        raise requests.ConnectionError("offline")
    monkeypatch.setattr("yaffo.routes.api.requests.get", offline)

    response = client.get("/api/locations", query_string={"q": "lis"})

    assert response.get_json() == {
        "results": [{"name": "Lisbon", "lat": 38.7, "lon": -9.1, "source": "photos"}],
    }


def test_short_location_query_returns_nothing(client):
    assert client.get("/api/locations", query_string={"q": "l"}).get_json() == {"results": []}
