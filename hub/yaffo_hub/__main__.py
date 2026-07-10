from __future__ import annotations

import argparse
import dataclasses
import logging

import uvicorn

from .config import HubSettings
from .hub import create_app


def main() -> None:
    env_defaults = HubSettings.from_env()
    parser = argparse.ArgumentParser(
        description="Yaffo P2P hub: WebSocket signaling + UDP relay + STUN. "
        "Every flag also has a YAFFO_HUB_<NAME> environment variable; flags win."
    )
    parser.add_argument("--host", default=env_defaults.host, help="Bind address for WebSocket/HTTP (loopback in production — Caddy terminates TLS in front)")
    parser.add_argument("--port", type=int, default=env_defaults.port, help="TCP port for WebSocket signaling")
    parser.add_argument("--relay-host", default=env_defaults.relay_host, help="Bind address for the UDP relay + STUN")
    parser.add_argument("--relay-port", type=int, default=env_defaults.relay_port, help="UDP port for the datagram relay + STUN")
    parser.add_argument("--max-session-bytes", type=int, default=env_defaults.max_session_bytes)
    parser.add_argument("--max-sessions-per-device", type=int, default=env_defaults.max_sessions_per_device)
    parser.add_argument("--connects-per-minute-per-ip", type=int, default=env_defaults.connects_per_minute_per_ip)
    parser.add_argument("--log-level", default=env_defaults.log_level)
    args = parser.parse_args()

    settings = dataclasses.replace(
        env_defaults,
        host=args.host,
        port=args.port,
        relay_host=args.relay_host,
        relay_port=args.relay_port,
        max_session_bytes=args.max_session_bytes,
        max_sessions_per_device=args.max_sessions_per_device,
        connects_per_minute_per_ip=args.connects_per_minute_per_ip,
        log_level=args.log_level,
    )

    logging.basicConfig(level=settings.log_level.upper())
    logging.getLogger("yaffo_hub").info(
        "event=starting signaling=%s:%d relay=udp:%s:%d", settings.host, settings.port, settings.relay_host, settings.relay_port
    )
    uvicorn.run(
        create_app(settings),
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level,
        # Caddy proxies from localhost; trust its X-Forwarded-For so the
        # per-IP rate limiter sees real client addresses, not 127.0.0.1.
        proxy_headers=True,
        forwarded_allow_ips="127.0.0.1",
        ws_max_size=settings.max_ws_message_bytes,
    )


if __name__ == "__main__":
    main()
