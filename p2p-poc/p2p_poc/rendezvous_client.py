from __future__ import annotations

import json
import urllib.error
import urllib.request


def register(rendezvous_url: str, device_id: str, host: str, port: int) -> None:
    """Announce this device's current address. In a real system this would

    re-run periodically (a heartbeat, since presence is dynamic); this POC
    registers once at startup for simplicity.
    """
    body = json.dumps({"device_id": device_id, "host": host, "port": port}).encode("utf-8")
    request = urllib.request.Request(
        f"{rendezvous_url.rstrip('/')}/register", data=body, headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(request, timeout=3) as response:
        response.read()


def lookup(rendezvous_url: str, device_id: str) -> tuple[str, int] | None:
    """Resolve a device's current address. Returns None on any failure

    (unknown device, unreachable service, timeout) so callers can fall back
    to whatever address hint they already have.
    """
    try:
        with urllib.request.urlopen(f"{rendezvous_url.rstrip('/')}/lookup/{device_id}", timeout=3) as response:
            data = json.loads(response.read())
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError):
        return None
    return data["host"], data["port"]
