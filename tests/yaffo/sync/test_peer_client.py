"""PeerClient: HTTP discovery through the web heartbeat, the in-process transport
through the real internal routes, and error mapping."""
import json
import time

import pytest

from yaffo.app import create_app
from yaffo.db import db
from yaffo.sync.peer_client import PeerClient, PeerClientError, default_transport, http_transport, in_process_transport

pytestmark = pytest.mark.unit


def test_no_heartbeat_means_offline(tmp_path):
    client = PeerClient(http_transport(data_dir=tmp_path))
    with pytest.raises(PeerClientError) as caught:
        client.status()
    assert caught.value.code == "peer_offline"


def test_a_stale_heartbeat_means_offline(tmp_path):
    (tmp_path / "web_status.json").write_text(json.dumps({
        "pid": 1, "started_at": 0, "beat_at": time.time() - 600, "healthy": True,
        "extra": {"internal_api": {"port": 5001, "version": 1}},
    }))
    with pytest.raises(PeerClientError) as caught:
        PeerClient(http_transport(data_dir=tmp_path)).status()
    assert caught.value.code == "peer_offline"


def test_a_refused_connection_means_offline(tmp_path):
    (tmp_path / "web_status.json").write_text(json.dumps({
        "pid": 1, "started_at": 0, "beat_at": time.time(), "healthy": True,
        "extra": {"internal_api": {"port": 1, "version": 1}},
    }))
    with pytest.raises(PeerClientError) as caught:
        PeerClient(http_transport(data_dir=tmp_path)).status()
    assert caught.value.code == "peer_offline"


@pytest.fixture
def app(tmp_path):
    application = create_app(db_path=tmp_path / "client.db", config={"TESTING": True})
    with application.app_context():
        db.create_all()
    return application


def test_in_process_calls_go_through_the_real_routes(app):
    client = PeerClient(in_process_transport(app))
    assert client.status() == {"available": False, "device_id": None, "hub_connected": False, "version": 1}
    with pytest.raises(PeerClientError) as caught:
        client.devices()
    assert caught.value.code == "engine_unavailable"


def test_the_web_process_uses_itself_and_workers_use_http(app):
    from types import SimpleNamespace

    assert default_transport().__qualname__.startswith("http_transport")
    app.extensions["p2p_service"] = SimpleNamespace(identity=None)
    with app.app_context():
        assert default_transport().__qualname__.startswith("in_process_transport")
