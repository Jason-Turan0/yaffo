import time
from types import SimpleNamespace

import pytest

from yaffo.p2p.lan_discovery import LanCandidate, ZeroconfLanDiscovery

pytestmark = pytest.mark.unit


def test_expired_candidate_is_probeable_but_not_reported_local_until_refreshed():
    discovery = ZeroconfLanDiscovery(
        SimpleNamespace(device_id="LOCAL"),
        quic_port=12345,
        candidate_ttl_seconds=1.0,
    )
    stale_candidate = LanCandidate(
        device_id="PEER",
        host="192.168.1.25",
        port=12345,
        name="PEER._yaffo-p2p._udp.local.",
        updated_at=time.monotonic() - 5.0,
    )
    discovery._candidates["PEER"] = stale_candidate
    discovery._service_names[stale_candidate.name] = "PEER"

    assert discovery.candidate_for("PEER") == stale_candidate
    assert discovery.reachable_device_ids() == set()
    assert discovery.candidate_for("PEER") == stale_candidate

    discovery.mark_reachable("PEER")

    refreshed = discovery.candidate_for("PEER")
    assert refreshed is not None
    assert refreshed.updated_at > stale_candidate.updated_at
    assert discovery.reachable_device_ids() == {"PEER"}
