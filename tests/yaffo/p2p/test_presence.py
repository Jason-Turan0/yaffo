"""device_online: emitted when a trusted paired device goes from absent to present
on the hub or the LAN, after a silent first sample."""
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from yaffo.db.models import EVENT_DEVICE_ONLINE, AutomationTrigger
from yaffo.background_tasks.event_filters import trigger_matches
from yaffo.background_tasks.events import EventContext
from yaffo.p2p.presence import PATH_HUB, PATH_LAN, PresenceWatcher

pytestmark = pytest.mark.unit

TRUSTED = {"MOM", "DAD"}


class FakeService:
    @contextmanager
    def _session(self):
        yield None


@pytest.fixture
def watcher(monkeypatch):
    emitted = []
    monkeypatch.setattr(
        "yaffo.p2p.presence.p2p_repository.get_known_device",
        lambda session, device_id: SimpleNamespace(trust_state="trusted" if device_id in TRUSTED else "revoked"),
    )
    w = PresenceWatcher(FakeService(), emit=lambda device_id, path: emitted.append((device_id, path)))
    w.emitted = emitted
    return w


def test_the_first_sample_is_a_baseline(watcher):
    assert watcher.observe(PATH_HUB, {"MOM"}) == []
    assert watcher.observe(PATH_LAN, {"DAD"}) == []
    assert watcher.emitted == []


def test_a_device_appearing_fires_once(watcher):
    watcher.observe(PATH_HUB, set())
    watcher.observe(PATH_LAN, set())

    watcher.observe(PATH_HUB, {"MOM"})
    watcher.observe(PATH_HUB, {"MOM"})

    assert watcher.emitted == [("MOM", "hub")]


def test_present_on_either_path_is_present(watcher):
    watcher.observe(PATH_HUB, set())
    watcher.observe(PATH_LAN, {"MOM"})
    watcher.observe(PATH_HUB, {"MOM"})  # already on the LAN: not "online again"
    assert watcher.emitted == []

    watcher.observe(PATH_HUB, set())
    watcher.observe(PATH_LAN, set())
    watcher.observe(PATH_LAN, {"MOM"})
    assert watcher.emitted == [("MOM", "lan")]


def test_unknown_hub_presence_is_not_absence(watcher):
    watcher.observe(PATH_HUB, {"MOM"})
    watcher.observe(PATH_LAN, set())
    watcher.observe(PATH_HUB, None)
    watcher.observe(PATH_HUB, {"MOM"})
    assert watcher.emitted == []


def test_untrusted_devices_never_fire(watcher):
    watcher.observe(PATH_HUB, set())
    watcher.observe(PATH_HUB, {"STRANGER"})
    assert watcher.emitted == []


@pytest.mark.parametrize(("config", "device_id", "matches"), [
    ({"device_id": "MOM"}, "MOM", True),
    ({"device_id": "MOM"}, "DAD", False),
    ({}, "DAD", True),
])
def test_device_online_triggers_filter_on_their_device(config, device_id, matches):
    trigger = AutomationTrigger(event_type=EVENT_DEVICE_ONLINE, config=config)
    context = EventContext(event_type=EVENT_DEVICE_ONLINE, device_id=device_id)
    assert trigger_matches(trigger, context) is matches
