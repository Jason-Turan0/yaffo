from __future__ import annotations

import asyncio
import json
import socket
import ssl
import time
from dataclasses import dataclass
from typing import Callable, Optional, Union

from aioquic.asyncio import connect
from aioquic.asyncio.protocol import QuicConnectionProtocol
from aioquic.asyncio.server import QuicServer
from aioquic.quic.configuration import QuicConfiguration
from aioquic.quic.connection import QuicConnection

from . import relay, stun_client
from .identity import DeviceIdentity, fingerprint_of_x509_cert

# QUIC requires ALPN; this just needs to match between client and server —
# it's not a real registered protocol id, just our own app's handshake tag.
ALPN_PROTOCOL = "yaffo-p2p-poc"
STREAM_TIMEOUT_SECONDS = 10.0
# UDP has no TCP-style "connection refused" — a dead/unreachable peer just
# produces silence, and aioquic's own idle_timeout defaults to a full 60s.
# Without an explicit cap here, a bad address hangs far longer than makes
# sense for a pairing attempt a human is waiting on.
CONNECT_TIMEOUT_SECONDS = 2.0

STUN_TIMEOUT_SECONDS = 5.0
PUNCH_INTERVAL_SECONDS = 0.25
PUNCH_PAYLOAD = b"yaffo-p2p-poc-punch"
RELAY_HELLO_TIMEOUT_SECONDS = 5.0
RELAY_HELLO_INTERVAL_SECONDS = 0.5

ConfirmHandler = Callable[[dict], dict]


class TransportError(Exception):
    pass


class PunchError(Exception):
    pass


@dataclass
class PunchResult:
    peer_observed_from: tuple[str, int]  # actual source address the first inbound punch packet arrived from


async def resolve_ipv4(host: str, port: int) -> tuple[str, int]:
    """uvloop's transport.sendto() needs a pre-resolved IP — unlike a
    blocking socket's sendto(), it won't do DNS resolution inline. Callers
    that key routing tables by remote address also need the resolved form.
    """
    loop = asyncio.get_running_loop()
    addrinfo = await loop.getaddrinfo(host, port, family=socket.AF_INET, type=socket.SOCK_DGRAM)
    return addrinfo[0][4]


class P2PDatagramMixin:
    """STUN discovery, hole punching, and relay HELLO/ACK for any protocol

    that owns a datagram transport (self._transport). Both the QUIC pairing
    server and the outbound-call dialer need the identical machinery, and
    in both cases it must ride the *same* socket as the QUIC traffic — a
    NAT maps each local port separately, so learning our address (STUN) or
    punching a hole on any *other* socket would say nothing about the port
    peers actually reach us on.
    """

    def _init_p2p(self) -> None:
        self._stun_waiters: dict[bytes, asyncio.Future] = {}
        self._punch_waiter: Optional[asyncio.Future] = None
        self._punch_expected_host: Optional[str] = None
        self._punch_send_task: Optional[asyncio.Task] = None
        self._relay_ack_waiters: dict[str, asyncio.Future] = {}

    def _intercept_datagram(self, data: Union[bytes, str], addr) -> bool:
        """True if this datagram was one of ours (STUN response, relay ACK,
        punch packet) and must not reach the QUIC layer."""
        if not isinstance(data, bytes):
            return False

        transaction_id = stun_client.transaction_id_of(data)
        if transaction_id is not None and transaction_id in self._stun_waiters:
            waiter = self._stun_waiters.pop(transaction_id)
            if not waiter.done():
                waiter.set_result(data)
            return True

        ack_token = relay.parse_ack(data)
        if ack_token is not None and ack_token in self._relay_ack_waiters:
            waiter = self._relay_ack_waiters[ack_token]
            if not waiter.done():
                waiter.set_result(addr)
            return True

        if data == PUNCH_PAYLOAD:
            if (
                self._punch_waiter is not None
                and not self._punch_waiter.done()
                and (self._punch_expected_host is None or addr[0] == self._punch_expected_host)
            ):
                self._punch_waiter.set_result(addr)
            return True  # swallow punch packets even with no waiter (peer may punch longer than us)

        return False

    async def discover_public_address(
        self, stun_host: str = "stun.l.google.com", stun_port: int = 19302, timeout: float = STUN_TIMEOUT_SECONDS
    ) -> tuple[str, int]:
        request, transaction_id = stun_client.build_binding_request()
        resolved_addr = await resolve_ipv4(stun_host, stun_port)
        waiter: asyncio.Future = asyncio.get_running_loop().create_future()
        self._stun_waiters[transaction_id] = waiter
        try:
            self._transport.sendto(request, resolved_addr)
            response = await asyncio.wait_for(waiter, timeout=timeout)
        finally:
            self._stun_waiters.pop(transaction_id, None)
        return stun_client.parse_binding_response(response, transaction_id)

    async def relay_hello(
        self, relay_host: str, relay_port: int, token: str, timeout: float = RELAY_HELLO_TIMEOUT_SECONDS
    ) -> None:
        """Announce this socket as one side of a relay session, retrying
        until the relay ACKs (UDP — the first HELLO can simply be lost)."""
        resolved_addr = await resolve_ipv4(relay_host, relay_port)
        hello = relay.build_hello(token)
        waiter: asyncio.Future = asyncio.get_running_loop().create_future()
        self._relay_ack_waiters[token] = waiter
        deadline = time.monotonic() + timeout

        async def sender() -> None:
            while time.monotonic() < deadline:
                self._transport.sendto(hello, resolved_addr)
                await asyncio.sleep(RELAY_HELLO_INTERVAL_SECONDS)

        send_task = asyncio.ensure_future(sender())
        try:
            await asyncio.wait_for(waiter, timeout=timeout)
        except asyncio.TimeoutError:
            raise TransportError(f"relay at {relay_host}:{relay_port} did not acknowledge within {timeout}s") from None
        finally:
            send_task.cancel()
            self._relay_ack_waiters.pop(token, None)

    async def punch(self, peer_host: str, peer_port: int, duration: float = 6.0) -> PunchResult:
        loop = asyncio.get_running_loop()
        self._punch_waiter = loop.create_future()
        self._punch_expected_host = peer_host
        deadline = time.monotonic() + duration

        async def sender() -> None:
            while time.monotonic() < deadline:
                if self._transport.is_closing():
                    return
                self._transport.sendto(PUNCH_PAYLOAD, (peer_host, peer_port))
                await asyncio.sleep(PUNCH_INTERVAL_SECONDS)

        # The sender deliberately runs for the FULL duration, even after our
        # own waiter has already fired: receiving one packet only proves the
        # peer->us direction. The peer's waiter may not even be armed yet
        # (e.g. the caller punches later than the callee, after its relay
        # phase) — going quiet on first success would starve it and one side
        # would report failure while the other reports success.
        send_task = asyncio.ensure_future(sender())
        self._punch_send_task = send_task  # keep a reference; outlives this call by design
        try:
            remaining = deadline - time.monotonic()
            addr = await asyncio.wait_for(self._punch_waiter, timeout=max(remaining, 0))
            return PunchResult(peer_observed_from=addr)
        except asyncio.TimeoutError:
            send_task.cancel()
            raise PunchError(f"no inbound packet from {peer_host} within {duration}s — hole punch failed") from None
        finally:
            self._punch_waiter = None
            self._punch_expected_host = None


class PunchAwareQuicServer(P2PDatagramMixin, QuicServer):
    """A QuicServer that can also speak STUN, hole-punch, and join relay

    sessions — all sharing the *same* listening socket as the actual QUIC
    traffic (see P2PDatagramMixin for why that's mandatory).

    Non-QUIC datagrams land in the same datagram_received() callback as
    real QUIC packets — aioquic already tolerates unparseable data
    gracefully (pull_quic_header raises, caught, silently dropped), so
    intercepting our own formats first and falling through to super() for
    everything else is safe.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._init_p2p()

    def datagram_received(self, data: Union[bytes, str], addr) -> None:
        if self._intercept_datagram(data, addr):
            return
        super().datagram_received(data, addr)


class P2PDialer(P2PDatagramMixin, asyncio.DatagramProtocol):
    """The caller side of a relay-first call: one UDP socket that can STUN,

    join a relay session, hole-punch, and carry *multiple sequential QUIC
    client connections* to different remote addresses — first to the relay,
    then (if the punch lands) directly to the peer. One socket for all of
    it is the whole point: the NAT mapping the punch opens belongs to this
    socket, so the direct connection must originate from it too.

    Inbound datagrams are routed to the right QUIC connection by remote
    address — exactly the property the relay-then-direct sequence needs,
    since the two paths have different remote addresses by definition.
    """

    def __init__(self) -> None:
        self._init_p2p()
        self._transport: Optional[asyncio.DatagramTransport] = None
        self._routes: dict[tuple[str, int], QuicConnectionProtocol] = {}

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self._transport = transport  # type: ignore[assignment]

    def datagram_received(self, data: Union[bytes, str], addr) -> None:
        if self._intercept_datagram(data, addr):
            return
        protocol = self._routes.get((addr[0], addr[1]))
        if protocol is not None:
            protocol.datagram_received(data, addr)

    def close(self) -> None:
        if self._transport is not None:
            self._transport.close()

    async def quic_pinned_request(
        self,
        host: str,
        port: int,
        expected_device_id: str,
        payload: dict,
        timeout: float = CONNECT_TIMEOUT_SECONDS,
    ) -> dict:
        """quic_pinned_confirm's fingerprint-pinning exchange, but on this

        shared socket instead of a fresh one. Mirrors what
        aioquic.asyncio.connect() does internally (build QuicConnection +
        QuicConnectionProtocol, connect, wait), with the protocol wired to
        our existing transport rather than a new datagram endpoint.
        """
        config = QuicConfiguration(
            is_client=True, alpn_protocols=[ALPN_PROTOCOL], verify_mode=ssl.CERT_NONE, server_name=host
        )
        protocol = QuicConnectionProtocol(QuicConnection(configuration=config))
        protocol.connection_made(self._transport)
        addr = (host, port)
        self._routes[addr] = protocol
        try:
            async with asyncio.timeout(timeout):
                protocol.connect(addr)
                await protocol.wait_connected()
                return await _pinned_stream_exchange(protocol, expected_device_id, payload)
        except TransportError:
            raise
        except (OSError, asyncio.TimeoutError, TimeoutError, ConnectionError) as exc:
            raise TransportError(f"could not reach peer at {host}:{port}: {exc}") from exc
        finally:
            protocol.close()
            # Deliberately not awaiting wait_closed(): the CONNECTION_CLOSE
            # is already transmitted by close(), and dropping the route now
            # just discards the peer's own close packets — harmless.
            self._routes.pop(addr, None)


async def start_dialer(bind_host: str = "0.0.0.0") -> P2PDialer:
    loop = asyncio.get_running_loop()
    _, protocol = await loop.create_datagram_endpoint(P2PDialer, local_addr=(bind_host, 0))
    return protocol


async def start_quic_pairing_server(
    bind_host: str, port: int, identity: DeviceIdentity, handler: ConfirmHandler
) -> PunchAwareQuicServer:
    """Serve the peer-to-peer pairing/confirm exchange over QUIC/UDP.

    Deliberately separate from the FastAPI/uvicorn HTTPS server (which stays
    TCP-based for the browser UI, human interaction only) — this is only the
    device-to-device data path, the one a real deployment would run over a
    hole-punched UDP connection instead of a directly-dialed TCP socket.
    """
    config = QuicConfiguration(is_client=False, alpn_protocols=[ALPN_PROTOCOL])
    config.load_cert_chain(str(identity.cert_path), str(identity.key_path))

    async def handle_one_stream(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            raw = await asyncio.wait_for(reader.read(), timeout=STREAM_TIMEOUT_SECONDS)
            request = json.loads(raw)
            response = handler(request)
        except Exception as exc:  # noqa: BLE001 — always reply, never leave the peer hanging
            response = {"status": "error", "detail": str(exc)}
        writer.write(json.dumps(response).encode("utf-8"))
        writer.write_eof()
        await writer.drain()

    def stream_handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        # aioquic calls this synchronously — unlike asyncio.start_server, it
        # does NOT auto-schedule a coroutine passed here, so schedule it
        # ourselves or the handler body silently never runs.
        asyncio.ensure_future(handle_one_stream(reader, writer))

    # Mirrors aioquic.asyncio.server.serve()'s own implementation, swapping
    # in PunchAwareQuicServer — serve() itself hardcodes plain QuicServer
    # with no way to inject a subclass.
    loop = asyncio.get_running_loop()
    _, protocol = await loop.create_datagram_endpoint(
        lambda: PunchAwareQuicServer(configuration=config, create_protocol=QuicConnectionProtocol, stream_handler=stream_handler),
        local_addr=(bind_host, port),
    )
    return protocol


async def _pinned_stream_exchange(protocol: QuicConnectionProtocol, expected_device_id: str, payload: dict) -> dict:
    """The trust-critical half of a client connection, shared by both
    dialing paths (fresh-socket quic_pinned_confirm and shared-socket
    P2PDialer): pin the peer's certificate fingerprint against
    expected_device_id, then exchange one JSON request/response stream.
    """
    peer_cert = protocol._quic.tls._peer_certificate
    if peer_cert is None:
        raise TransportError("peer presented no certificate")

    actual_device_id = fingerprint_of_x509_cert(peer_cert)
    if actual_device_id != expected_device_id:
        raise TransportError(
            f"certificate mismatch: expected {expected_device_id}, "
            f"peer presented {actual_device_id} — aborting (possible MITM)"
        )

    reader, writer = await protocol.create_stream()
    writer.write(json.dumps(payload).encode("utf-8"))
    writer.write_eof()
    await writer.drain()

    raw = await reader.read()
    data = json.loads(raw)
    if data.get("status") != "ok":
        raise TransportError(data.get("detail") or data.get("message") or "peer rejected request")
    return data


async def quic_pinned_confirm(host: str, port: int, expected_device_id: str, payload: dict) -> dict:
    """Client side: connect to a peer over QUIC, pin its certificate

    fingerprint against expected_device_id — the same pinning idea as the
    original TCP transport, just over UDP — then exchange the confirm
    payload on a QUIC stream.
    """
    config = QuicConfiguration(is_client=True, alpn_protocols=[ALPN_PROTOCOL], verify_mode=ssl.CERT_NONE)
    try:
        async with asyncio.timeout(CONNECT_TIMEOUT_SECONDS):
            async with connect(host, port, configuration=config) as protocol:
                return await _pinned_stream_exchange(protocol, expected_device_id, payload)
    except TransportError:
        raise
    except (OSError, asyncio.TimeoutError, TimeoutError, ConnectionError) as exc:
        raise TransportError(f"could not reach peer at {host}:{port}: {exc}") from exc
