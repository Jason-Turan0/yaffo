"""The remote gallery's filter panel is configurable like the home gallery's.
There is one app-wide layout (order + visibility) — a purely local preference;
nothing about it travels over the p2p protocol."""
from yaffo.db import db
from yaffo.db.models import KnownDevice


def _add_device() -> str:
    device = KnownDevice(device_id="peer-1", pubkey="pk", display_name="Peer One")
    db.session.add(device)
    db.session.commit()
    return device.device_id


def _files_page(client, device_id: str) -> str:
    response = client.get(f"/sharing/devices/{device_id}/files?media_dir_id=dir-1&scope=&label=")
    assert response.status_code == 200
    return response.data.decode()


def test_remote_gallery_renders_filter_config(app, client):
    device_id = _add_device()

    body = _files_page(client, device_id)

    assert 'id="configure-filters-btn"' in body
    assert 'id="filter-config-list"' in body
    # The scope coordinates still ride the filter form as hidden inputs.
    assert 'name="media_dir_id"' in body


def test_saved_layout_drives_remote_panel(app, client):
    device_id = _add_device()

    response = client.post(
        "/settings/filters",
        json={"items": [{"key": "year", "visible": False}, {"key": "month", "visible": True}]},
    )
    assert response.status_code == 204

    body = _files_page(client, device_id)

    assert 'id="year-select"' not in body
    assert 'id="month-select"' in body


def test_layout_is_shared_with_home(app, client):
    device_id = _add_device()

    response = client.post(
        "/settings/filters",
        json={"items": [{"key": "year", "visible": False}]},
    )
    assert response.status_code == 204

    # One layout for the whole app: hiding a filter hides it everywhere.
    assert 'id="year-select"' not in client.get("/").data.decode()
    assert 'id="year-select"' not in _files_page(client, device_id)
