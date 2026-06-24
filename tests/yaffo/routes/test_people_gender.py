import pytest

from yaffo.db import db
from yaffo.db.models import Person

pytestmark = pytest.mark.unit


def test_people_page_renders_clearable_gender_dropdowns(client):
    response = client.get("/people")

    assert response.status_code == 200
    body = response.data.decode()
    assert 'id="addPersonGender"' in body
    assert 'id="editPersonGender"' in body
    assert body.count('<option value="">Not specified</option>') == 2
    assert "Choose “Not specified” to clear the saved gender." in body


def test_create_person_saves_selected_gender(client, app):
    response = client.post(
        "/people/create",
        data={"name": "Alex", "gender": "1"},
    )

    assert response.status_code == 302
    with app.app_context():
        assert Person.query.filter_by(name="Alex").one().gender == 1


def test_update_person_can_clear_gender(client, app, monkeypatch):
    with app.app_context():
        person = Person(name="Alex", gender=0)
        db.session.add(person)
        db.session.commit()
        person_id = person.id

    monkeypatch.setattr("yaffo.routes.people.get_media_item_ids_for_person", lambda *args: [])

    response = client.post(
        f"/people/{person_id}/update",
        data={"name": "Alex", "gender": "", "birthdate": ""},
    )

    assert response.status_code == 302
    with app.app_context():
        assert db.session.get(Person, person_id).gender is None
