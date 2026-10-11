"""`device_online`: a paired device went from absent to present.

Peer syncs import when the device they import from shows up (a laptop opened once
a week imports when it's opened, not at the next nightly run). The watcher runs on
the P2P engine loop and samples presence: the LAN (mDNS, in memory) every
LAN_INTERVAL_SECONDS, the hub every HUB_EVERY_TICKS of those (it's a hub request).
A device counts as present when either path sees it, so one appearing on the hub
while already on the LAN is not "online again". The first sample is a baseline:
devices already present when Yaffo starts don't fire. An unreachable hub keeps the
previous hub sample (unknown isn't absent).

Payload `{"device_id": ..., "path": "hub" | "lan"}`; peer syncs' own trigger filters
on `device_id` and settles, so a flapping device is one run.
"""
from __future__ import annotations

import asyncio
from typing import Callable, Optional

from yaffo.db.models import EVENT_DEVICE_ONLINE, TRUST_STATE_TRUSTED
from yaffo.db.repositories import p2p_repository
from yaffo.logging_config import get_logger

logger = get_logger(__name__, "webapp")

LAN_INTERVAL_SECONDS = 30.0
HUB_EVERY_TICKS = 6  # every 3 minutes

PATH_HUB = "hub"
PATH_LAN = "lan"


def emit_device_online(device_id: str, path: str) -> None:
    from yaffo.background_tasks.events import emit_event

    emit_event(EVENT_DEVICE_ONLINE, {"device_id": device_id, "path": path})


class PresenceWatcher:
    def __init__(self, service, emit: Callable[[str, str], None] = emit_device_online) -> None:
        self._service = service
        self._emit = emit
        self._seen: dict[str, Optional[set[str]]] = {PATH_HUB: None, PATH_LAN: None}

    def _present(self) -> set[str]:
        return set().union(*(seen for seen in self._seen.values() if seen is not None))

    def _trusted(self, device_ids: set[str]) -> set[str]:
        if not device_ids:
            return set()
        with self._service._session() as session:
            return {device_id for device_id in device_ids
                    if (device := p2p_repository.get_known_device(session, device_id)) is not None
                    and device.trust_state == TRUST_STATE_TRUSTED}

    def observe(self, path: str, present: Optional[set[str]]) -> list[str]:
        """Record one sample of `path` (None: unknown, keep the last one) and emit
        for the trusted devices that just appeared. Returns them."""
        if present is None:
            return []
        baseline = self._seen[path] is None
        before = self._present()
        self._seen[path] = set(present)
        if baseline:
            return []
        appeared = sorted(self._trusted(self._present() - before))
        for device_id in appeared:
            logger.info("paired device %s came online (%s)", device_id, path)
            try:
                self._emit(device_id, path)
            except Exception:  # noqa: BLE001 — presence must never take the engine loop down
                logger.exception("could not emit device_online for %s", device_id)
        return appeared

    async def run(self, lan_interval: float = LAN_INTERVAL_SECONDS, hub_every: int = HUB_EVERY_TICKS) -> None:
        tick = 0
        while True:
            try:
                self.observe(PATH_LAN, self._service.local_device_ids())
                if tick % hub_every == 0:
                    self.observe(PATH_HUB, await self._hub_sample())
            except Exception:  # noqa: BLE001
                logger.exception("presence sample failed")
            tick += 1
            await asyncio.sleep(lan_interval)

    async def _hub_sample(self) -> Optional[set[str]]:
        hub = self._service._hub_client
        if hub is None or not hub.connected:
            return None
        try:
            return await hub.connected_device_ids()
        except Exception:  # noqa: BLE001 — unknown, not absent
            return None
