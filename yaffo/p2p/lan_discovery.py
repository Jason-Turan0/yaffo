"""mDNS LAN discovery for the p2p QUIC endpoint.

The hub remains the presence/signaling source of truth, but same-LAN peers can
be reached directly by their advertised QUIC endpoint. Discovery is only a
candidate source: every actual connection still uses the pinned QUIC handshake.
"""
from __future__ import annotations

import asyncio
import socket
import threading
import time
from dataclasses import dataclass, replace
from typing import Optional

from yaffo.logging_config import get_logger
from yaffo.p2p.identity import DeviceIdentity

try:  # pragma: no cover - exercised when the optional runtime dependency exists.
    from zeroconf import IPVersion, ServiceInfo
    from zeroconf.asyncio import AsyncServiceBrowser, AsyncServiceInfo, AsyncZeroconf
except ImportError:  # pragma: no cover - local dev/test venv may not have zeroconf installed yet.
    AsyncServiceBrowser = None
    AsyncServiceInfo = None
    AsyncZeroconf = None
    IPVersion = None
    ServiceInfo = None


logger = get_logger(__name__, "webapp")

SERVICE_TYPE = "_yaffo-p2p._udp.local."
LAN_CANDIDATE_TTL_SECONDS = 120.0


@dataclass(frozen=True)
class LanCandidate:
    device_id: str
    host: str
    port: int
    name: str
    updated_at: float


class NullLanDiscovery:
    available = False

    async def start(self) -> None:
        return

    async def stop(self) -> None:
        return

    def candidate_for(self, device_id: str) -> Optional[LanCandidate]:
        return None

    def reachable_device_ids(self) -> set[str]:
        return set()

    def mark_reachable(self, device_id: str) -> None:
        return


class ZeroconfLanDiscovery:
    available = True

    def __init__(
        self,
        identity: DeviceIdentity,
        quic_port: int,
        bind_host: str = "0.0.0.0",
        candidate_ttl_seconds: float = LAN_CANDIDATE_TTL_SECONDS,
    ) -> None:
        if AsyncZeroconf is None or AsyncServiceBrowser is None or AsyncServiceInfo is None or ServiceInfo is None:
            raise RuntimeError("zeroconf is not installed")
        self._identity = identity
        self._quic_port = quic_port
        self._bind_host = bind_host
        self._candidate_ttl_seconds = candidate_ttl_seconds
        self._lock = threading.Lock()
        self._candidates: dict[str, LanCandidate] = {}
        self._service_names: dict[str, str] = {}
        self._zeroconf = None
        self._browser = None
        self._service_info = None
        self._loop = None

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        addresses = _advertise_addresses(self._bind_host)
        self._zeroconf = AsyncZeroconf(ip_version=IPVersion.V4Only)
        if addresses:
            name = f"{self._identity.device_id}.{SERVICE_TYPE}"
            self._service_info = ServiceInfo(
                SERVICE_TYPE,
                name,
                addresses=addresses,
                port=self._quic_port,
                properties={
                    "device_id": self._identity.device_id,
                    "port": str(self._quic_port),
                },
                server=f"{socket.gethostname().rstrip('.')}.local.",
            )
            await self._zeroconf.async_register_service(self._service_info)
            logger.info("advertising p2p LAN endpoint via mDNS on udp/%s", self._quic_port)
        else:
            logger.warning("no IPv4 address available for p2p LAN advertisement; browsing only")
        self._browser = AsyncServiceBrowser(
            self._zeroconf.zeroconf,
            SERVICE_TYPE,
            listener=_LanServiceListener(self),
        )

    async def stop(self) -> None:
        if self._zeroconf is None:
            return
        try:
            if self._service_info is not None:
                await self._zeroconf.async_unregister_service(self._service_info)
        finally:
            await self._zeroconf.async_close()
            self._zeroconf = None
            self._browser = None
            self._service_info = None
            self._loop = None

    def candidate_for(self, device_id: str) -> Optional[LanCandidate]:
        with self._lock:
            return self._candidates.get(device_id)

    def reachable_device_ids(self) -> set[str]:
        now = time.monotonic()
        with self._lock:
            return {
                device_id
                for device_id, candidate in self._candidates.items()
                if now - candidate.updated_at <= self._candidate_ttl_seconds
            }

    def mark_reachable(self, device_id: str) -> None:
        with self._lock:
            candidate = self._candidates.get(device_id)
            if candidate is not None:
                self._candidates[device_id] = replace(candidate, updated_at=time.monotonic())

    def _cache_service(self, zeroconf, service_type: str, name: str) -> None:
        if self._loop is None:
            return
        asyncio.run_coroutine_threadsafe(self._resolve_service(zeroconf, service_type, name), self._loop)

    async def _resolve_service(self, zeroconf, service_type: str, name: str) -> None:
        info = AsyncServiceInfo(service_type, name)
        if not await info.async_request(zeroconf, timeout=1000):
            return
        props = _decode_properties(info.properties)
        device_id = props.get("device_id", "")
        if not device_id or device_id == self._identity.device_id:
            return
        addresses = info.parsed_addresses(IPVersion.V4Only)
        if not addresses:
            return
        candidate = LanCandidate(
            device_id=device_id,
            host=addresses[0],
            port=int(props.get("port") or info.port),
            name=name,
            updated_at=time.monotonic(),
        )
        with self._lock:
            self._candidates[device_id] = candidate
            self._service_names[name] = device_id

    def _remove_service(self, name: str) -> None:
        with self._lock:
            device_id = self._service_names.pop(name, None)
            if device_id:
                self._candidates.pop(device_id, None)


class _LanServiceListener:
    def __init__(self, discovery: ZeroconfLanDiscovery) -> None:
        self._discovery = discovery

    def add_service(self, zeroconf, service_type: str, name: str) -> None:
        self._discovery._cache_service(zeroconf, service_type, name)

    def update_service(self, zeroconf, service_type: str, name: str) -> None:
        self._discovery._cache_service(zeroconf, service_type, name)

    def remove_service(self, zeroconf, service_type: str, name: str) -> None:
        self._discovery._remove_service(name)


def create_lan_discovery(identity: DeviceIdentity, quic_port: int, bind_host: str = "0.0.0.0"):
    if AsyncZeroconf is None:
        logger.warning("zeroconf is not installed; p2p LAN discovery disabled")
        return NullLanDiscovery()
    return ZeroconfLanDiscovery(identity, quic_port, bind_host)


def _decode_properties(raw_properties: dict) -> dict[str, str]:
    decoded: dict[str, str] = {}
    for raw_key, raw_value in raw_properties.items():
        key = raw_key.decode("utf-8") if isinstance(raw_key, bytes) else str(raw_key)
        value = raw_value.decode("utf-8") if isinstance(raw_value, bytes) else str(raw_value)
        decoded[key] = value
    return decoded


def _advertise_addresses(bind_host: str) -> list[bytes]:
    hosts: set[str] = set()
    if bind_host and bind_host not in ("0.0.0.0", "::"):
        hosts.add(bind_host)
    else:
        hostname = socket.gethostname()
        try:
            for family, _type, _proto, _canonname, sockaddr in socket.getaddrinfo(hostname, None, socket.AF_INET):
                if family == socket.AF_INET:
                    hosts.add(sockaddr[0])
        except OSError:
            pass
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.connect(("8.8.8.8", 80))
                hosts.add(sock.getsockname()[0])
        except OSError:
            pass
    addresses: list[bytes] = []
    for host in sorted(hosts):
        try:
            addresses.append(socket.inet_aton(host))
        except OSError:
            continue
    return addresses
