from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass
class KnownDevice:
    device_id: str
    pubkey: str
    display_name: str
    trust_state: str  # "trusted", or "revoked" (the peer cut us off); revoking a peer *yourself* removes the row entirely
    paired_at: float


class KnownDevicesStore:
    """Paired peers, persisted per-device so trust survives a restart."""

    def __init__(self, path: Path):
        self._path = path
        if not self._path.exists():
            self._path.write_text("[]")

    def list(self) -> list[KnownDevice]:
        data = json.loads(self._path.read_text())
        return [KnownDevice(**d) for d in data]

    def add(self, device: KnownDevice) -> None:
        devices = [d for d in self.list() if d.device_id != device.device_id]
        devices.append(device)
        self._path.write_text(json.dumps([asdict(d) for d in devices], indent=2))

    def revoke(self, device_id: str) -> None:
        devices = [d for d in self.list() if d.device_id != device_id]
        self._path.write_text(json.dumps([asdict(d) for d in devices], indent=2))

    def mark_revoked(self, device_id: str) -> None:
        """The *receiving* side of a revocation: the peer cut us off, so
        flip our row to "revoked" (kept visible so the UI can say why
        things stopped working) rather than deleting it. Never creates a
        row. A successful re-pairing overwrites it back to trusted via
        add().
        """
        devices = self.list()
        for device in devices:
            if device.device_id == device_id:
                device.trust_state = "revoked"
        self._path.write_text(json.dumps([asdict(d) for d in devices], indent=2))
