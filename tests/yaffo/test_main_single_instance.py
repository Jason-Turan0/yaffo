import socket

import pytest

from yaffo import __main__ as entry
from yaffo.scripts.db import migrate

pytestmark = pytest.mark.unit


@pytest.fixture
def held_port(monkeypatch):
    """A port another 'instance' already owns, with HOST/PORT pointed at it."""
    monkeypatch.setattr(entry, "HOST", "127.0.0.1")
    monkeypatch.setattr(entry, "PORT", 0)
    first = entry._bind_listen_socket()
    assert first is not None
    monkeypatch.setattr(entry, "PORT", first.getsockname()[1])
    yield first
    first.close()


def test_bind_listen_socket_is_listening_immediately(held_port):
    with socket.create_connection(held_port.getsockname(), timeout=2):
        pass


def test_second_bind_on_same_port_fails(held_port):
    assert entry._bind_listen_socket() is None


def test_second_instance_opens_browser_and_starts_nothing(held_port, monkeypatch):
    opened = []
    monkeypatch.delenv("YAFFO_DEMO_MODE", raising=False)
    monkeypatch.setattr(entry.webbrowser, "open", opened.append)
    monkeypatch.setattr(migrate, "run_migrations", lambda: pytest.fail("second instance ran migrations"))
    monkeypatch.setattr(entry, "_start_background", lambda: pytest.fail("second instance started children"))

    entry._run_web()

    assert opened == [f"http://127.0.0.1:{entry.PORT}"]
