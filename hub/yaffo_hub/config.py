from __future__ import annotations

import os
from dataclasses import dataclass, fields


@dataclass(frozen=True)
class HubSettings:
    """All knobs in one place, overridable per-field via YAFFO_HUB_* env vars
    (see `from_env`) and then via CLI flags (see __main__). Defaults suit the
    production shape: HTTP/WebSocket bound to loopback behind Caddy, the UDP
    relay bound to all interfaces (it terminates no TLS — the datagrams it
    forwards are already end-to-end encrypted QUIC).
    """

    host: str = "127.0.0.1"
    port: int = 8080
    relay_host: str = "0.0.0.0"
    relay_port: int = 40000

    # Auth + brokered-token lifetimes.
    auth_timeout_seconds: float = 10.0
    token_ttl_seconds: float = 60.0

    # Relay limits: idle sessions expire; a session that forwards more than
    # max_session_bytes stops forwarding (abuse backstop, not a feature
    # limit — a legitimate multi-GB pull that lost the punch should fit).
    session_ttl_seconds: float = 600.0
    max_session_bytes: int = 8 * 2**30

    # Per-device / per-IP abuse limits on the signaling side.
    max_sessions_per_device: int = 8
    connects_per_minute_per_ip: int = 30

    # Signaling messages are small JSON blobs; anything bigger is bogus.
    max_ws_message_bytes: int = 64 * 1024

    log_level: str = "info"

    @classmethod
    def from_env(cls) -> "HubSettings":
        values: dict = {}
        for field in fields(cls):
            raw = os.environ.get(f"YAFFO_HUB_{field.name.upper()}")
            if raw is None:
                continue
            if field.type in ("int", int):
                values[field.name] = int(raw)
            elif field.type in ("float", float):
                values[field.name] = float(raw)
            else:
                values[field.name] = raw
        return cls(**values)
