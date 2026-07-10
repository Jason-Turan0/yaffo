from __future__ import annotations

import argparse
import asyncio
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Dict

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from . import rendezvous_client
from .identity import load_or_create_identity
from .pairing import PairingCode, PairingError, new_pairing_code, sign_nonce, verify_nonce_signature
from .quic_transport import PunchError, TransportError, quic_pinned_confirm, start_quic_pairing_server
from .sharing import (
    build_pull_request,
    build_revocation_notice,
    ensure_seed_files,
    read_shared_files,
    verify_pull_request,
    verify_revocation_notice,
)
from .signaling import CallError, HubClient
from .store import KnownDevice, KnownDevicesStore

BASE_DIR = Path(__file__).parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


class AcceptRequest(BaseModel):
    code: str


class PunchRequest(BaseModel):
    peer_device_id: str
    duration: float = 6.0


class CallRequest(BaseModel):
    peer_device_id: str
    punch_duration: float = 10.0


def create_app(
    data_dir: Path,
    host: str,
    port: int,
    rendezvous_url: str | None = None,
    bind_host: str | None = None,
    hub_url: str | None = None,
) -> FastAPI:
    identity = load_or_create_identity(data_dir, host)
    store = KnownDevicesStore(data_dir / "known_devices.json")
    pending: Dict[str, PairingCode] = {}
    bind_host = bind_host or host
    # Every device shares a small library of random text files (photo
    # stand-ins) with its paired peers — seeded once, stable thereafter.
    shared_dir = data_dir / "shared"
    ensure_seed_files(shared_dir)

    if rendezvous_url:
        rendezvous_client.register(rendezvous_url, identity.device_id, host, port)

    def handle_confirm(body: dict) -> dict:
        """Shared by the QUIC server (see lifespan below) — the actual

        device-to-device confirm exchange runs over QUIC/UDP now, not HTTP;
        this is the same trust logic (nonce lookup, expiry, signature) that
        used to live in an HTTP route, just no longer reachable over TCP.
        """
        code = pending.pop(body.get("nonce"), None)
        if code is None:
            return {"status": "error", "detail": "unknown or already-used pairing code"}
        if code.is_expired():
            return {"status": "error", "detail": "pairing code expired"}
        if not verify_nonce_signature(body["pubkey"], body["nonce"], body["signature"]):
            return {"status": "error", "detail": "signature verification failed"}

        store.add(
            KnownDevice(
                device_id=body["device_id"],
                pubkey=body["pubkey"],
                display_name=body["device_id"],
                trust_state="trusted",
                paired_at=time.time(),
            )
        )
        return {"status": "ok", "display_name": identity.device_id}

    def handle_stream_request(body: dict) -> dict:
        """Everything a peer can ask of us over a QUIC stream: pairing
        confirms (the trust exchange), a trivial ping (the path probe), and
        file pulls — the first real data sharing, restricted to paired
        peers via a signed request verified against our own trust store.
        """
        if body.get("type") == "ping":
            return {"status": "ok", "type": "pong", "device_id": identity.device_id}
        if body.get("type") == "pull_files":
            denial = verify_pull_request(body, store.list())
            if denial is not None:
                return {"status": "error", "detail": denial}
            return {"status": "ok", "device_id": identity.device_id, "files": read_shared_files(shared_dir)}
        return handle_confirm(body)

    def handle_revocation_notice(message: dict) -> None:
        """A peer says it revoked us. Only honored when the signature
        verifies against the pubkey we ALREADY hold for that device — an
        unsigned or unverifiable notice from the (unauthenticated) hub
        channel is ignored, so nobody can spoof-revoke someone else's
        pairing. Enforcement doesn't depend on this arriving at all; it
        exists so the UI can say why the peer stopped answering.
        """
        denial = verify_revocation_notice(message, store.list())
        if denial is None:
            store.mark_revoked(message["device_id"])

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # PunchAwareQuicServer: STUN/punch ride the *same* socket the QUIC
        # server already listens on — a NAT maps each local port
        # separately, so doing either on any other socket would say
        # nothing about the port peers actually try to reach for pairing.
        quic_server = await start_quic_pairing_server(bind_host, port, identity, handle_stream_request)
        app.state.quic_server = quic_server
        hub_client = None
        if hub_url:
            hub_client = HubClient(hub_url, identity, quic_server, on_revoked=handle_revocation_notice)
            hub_client.start()
        app.state.hub_client = hub_client
        try:
            yield
        finally:
            if hub_client is not None:
                await hub_client.stop()
            quic_server.close()

    app = FastAPI(title="Yaffo P2P Pairing POC", lifespan=lifespan)
    app.state.identity = identity
    app.state.store = store
    app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

    async def annotated_known_devices() -> list[dict]:
        """Known devices plus live presence: `connected` is True/False when
        a hub is configured (the hub's open WebSockets ARE presence), and
        None when there's no hub or it's unreachable — unknown, which is
        deliberately distinct from offline. Presence is display-only state;
        trust_state stays purely local and human-granted.
        """
        devices = [d.__dict__ for d in store.list()]
        hub_client = app.state.hub_client
        connected = await hub_client.connected_device_ids() if hub_client is not None else None
        for device in devices:
            device["connected"] = (device["device_id"] in connected) if connected is not None else None
        return devices

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            request,
            "index.html",
            {
                "device_id": identity.device_id,
                "host": host,
                "port": port,
                "known_devices": await annotated_known_devices(),
            },
        )

    @app.get("/api/device-info")
    def device_info() -> dict:
        return {"device_id": identity.device_id, "pubkey": identity.public_key_b64, "host": host, "port": port}

    @app.get("/api/known-devices")
    async def known_devices() -> list:
        return await annotated_known_devices()

    @app.delete("/api/known-devices/{device_id}")
    async def revoke_device(device_id: str) -> JSONResponse:
        """Revoke a paired peer: remove it from the local trust store —
        which is the enforcement, since every pull re-checks the store —
        and best-effort deliver a signed courtesy notice so the peer's UI
        shows "revoked" instead of unexplained denials. Soft revocation,
        per the design doc: stops all *future* access; anything the peer
        already fetched is theirs.
        """
        if not any(d.device_id == device_id for d in store.list()):
            return JSONResponse({"status": "error", "message": f"{device_id} is not a known device"}, status_code=404)

        hub_client = app.state.hub_client
        peer_notified = False
        if hub_client is not None and hub_client.connected:
            try:
                await hub_client.notify_revoked(device_id, build_revocation_notice(identity))
                peer_notified = True  # sent to the hub; delivery to an offline peer is not guaranteed
            except CallError:
                pass

        store.revoke(device_id)
        return JSONResponse({"status": "ok", "revoked": device_id, "peer_notified": peer_notified})

    @app.get("/api/shared-files")
    def shared_files() -> dict:
        return {"device_id": identity.device_id, "files": read_shared_files(shared_dir)}

    @app.get("/api/peers/{peer_device_id}/files")
    async def peer_files(peer_device_id: str) -> JSONResponse:
        """Pull a paired peer's shared files over the relay path (no punch
        wait — this is an interactive request, and the relay answer is
        already in hand). The signed pull request is authorized by the
        *peer* against its trust store; we fail fast here only for a
        clearly-impossible ask.
        """
        hub_client = app.state.hub_client
        if hub_client is None:
            return JSONResponse({"status": "error", "message": "no hub configured (--hub)"}, status_code=400)
        try:
            report = await hub_client.call(
                peer_device_id, payload=build_pull_request(identity), attempt_upgrade=False
            )
        except CallError as exc:
            return JSONResponse({"status": "error", "message": str(exc)}, status_code=502)
        return JSONResponse(
            {
                "status": "ok",
                "peer_device_id": report["response"].get("device_id", peer_device_id),
                "via": report["path"],
                "files": report["response"].get("files", []),
            }
        )

    @app.post("/api/pairing/generate")
    def generate_pairing() -> dict:
        code = new_pairing_code(identity.device_id, identity.public_key_b64, host, port)
        pending[code.nonce] = code
        return {"code": code.encode(), "expires_at": code.expires_at}

    @app.post("/api/pairing/accept")
    async def accept_pairing(body: AcceptRequest) -> JSONResponse:
        try:
            code = PairingCode.decode(body.code)
        except PairingError as exc:
            return JSONResponse({"status": "error", "message": str(exc)}, status_code=400)

        if code.is_expired():
            return JSONResponse({"status": "error", "message": "pairing code expired"}, status_code=400)
        if code.device_id == identity.device_id:
            return JSONResponse({"status": "error", "message": "cannot pair a device with itself"}, status_code=400)

        payload = {
            "device_id": identity.device_id,
            "pubkey": identity.public_key_b64,
            "nonce": code.nonce,
            "signature": sign_nonce(identity.private_key, code.nonce),
        }

        # Discovery is separate from trust. With a hub configured, the
        # confirm exchange rides the relay-first call flow — so pairing
        # works across NATs with no reachable address at all, and the code's
        # embedded host/port is irrelevant. Without a hub, fall back to the
        # original direct dial (rendezvous lookup, then the code's address
        # hint). Either way the trust check is identical and end-to-end:
        # fingerprint pinning + nonce signature inside the QUIC handshake —
        # the hub only ever forwards ciphertext.
        hub_client = app.state.hub_client
        try:
            if hub_client is not None:
                report = await hub_client.call(code.device_id, payload=payload)
                result = report["response"]
                via = report["path"]
            else:
                resolved_host, resolved_port = code.host, code.port
                if rendezvous_url:
                    current = rendezvous_client.lookup(rendezvous_url, code.device_id)
                    if current is not None:
                        resolved_host, resolved_port = current
                result = await quic_pinned_confirm(resolved_host, resolved_port, code.device_id, payload)
                via = "direct-dial"
        except (TransportError, CallError) as exc:
            return JSONResponse({"status": "error", "message": str(exc)}, status_code=400)

        store.add(
            KnownDevice(
                device_id=code.device_id,
                pubkey=code.pubkey,
                display_name=result.get("display_name", code.device_id),
                trust_state="trusted",
                paired_at=time.time(),
            )
        )
        return JSONResponse({"status": "ok", "peer_device_id": code.device_id, "via": via})

    @app.post("/api/call")
    async def call_peer(body: CallRequest) -> JSONResponse:
        """Relay-first call (the Tailscale pattern): coordinate over the

        hub's WebSocket, prove the call works through the UDP relay (this
        always succeeds if both devices are online — no NAT cooperation
        needed), then attempt the hole-punch upgrade to a direct path.
        The report says which path the call ended on; "relay" is a working
        call, "direct" is a working call that also punched through.
        """
        hub_client = app.state.hub_client
        if hub_client is None:
            return JSONResponse({"status": "error", "message": "no hub configured (--hub)"}, status_code=400)
        try:
            report = await hub_client.call(body.peer_device_id, punch_duration=body.punch_duration)
        except CallError as exc:
            return JSONResponse({"status": "error", "message": str(exc)}, status_code=502)
        return JSONResponse({"status": "ok", **report})

    @app.post("/api/punch")
    async def punch_peer(body: PunchRequest) -> JSONResponse:
        """The actual NAT-traversal proof: this device is presumed to have

        no directly reachable address (that's the whole scenario) — STUN
        tells it its own current NAT mapping, rendezvous carries that
        mapping to the peer and hands back the peer's, and punch() fires UDP
        at the peer's mapped address while listening for the peer doing the
        same back. Both sides need to call this at roughly the same time
        (the same "go" signal problem discussed for real hole-punching) —
        driving that timing is external to this endpoint, same as a real
        signaling exchange would be.
        """
        if not rendezvous_url:
            return JSONResponse({"status": "error", "message": "no rendezvous configured"}, status_code=400)

        quic_server = app.state.quic_server
        try:
            my_address = await quic_server.discover_public_address()
        except (OSError, asyncio.TimeoutError) as exc:
            return JSONResponse({"status": "error", "message": f"STUN query failed: {exc}"}, status_code=502)

        rendezvous_client.register(rendezvous_url, identity.device_id, my_address[0], my_address[1])

        peer_address = rendezvous_client.lookup(rendezvous_url, body.peer_device_id)
        if peer_address is None:
            return JSONResponse(
                {"status": "error", "message": f"{body.peer_device_id} has no registered address"}, status_code=404
            )

        try:
            result = await quic_server.punch(peer_address[0], peer_address[1], duration=body.duration)
        except PunchError as exc:
            return JSONResponse({"status": "error", "message": str(exc)}, status_code=504)

        return JSONResponse(
            {
                "status": "ok",
                "my_stun_address": list(my_address),
                "peer_registered_address": list(peer_address),
                "received_from": list(result.peer_observed_from),
            }
        )

    return app


def main() -> None:
    import uvicorn

    parser = argparse.ArgumentParser(description="Run one Yaffo P2P pairing POC device")
    parser.add_argument("--host", default="127.0.0.1", help="Address to advertise in pairing codes / rendezvous / the TLS cert's SAN")
    parser.add_argument(
        "--bind-host",
        default=None,
        help="Address to actually bind both sockets to (HTTPS/TCP for the UI and QUIC/UDP for pairing), "
        "if different from --host — e.g. a cloud VM's external IP isn't present on any local "
        "interface (it's a NAT mapping), so you bind 0.0.0.0 but advertise the external IP via "
        "--host. Defaults to --host.",
    )
    parser.add_argument("--port", type=int, required=True, help="Used for both the HTTPS UI (TCP) and pairing (QUIC/UDP) — same number, separate protocols")
    parser.add_argument("--data-dir", required=True, help="Per-device identity/state directory")
    parser.add_argument(
        "--rendezvous",
        default=None,
        help="Base URL of a rendezvous service (e.g. http://127.0.0.1:9000). "
        "If set, this device registers its address there and resolves peers "
        "by device_id instead of trusting a pairing code's embedded address.",
    )
    parser.add_argument(
        "--hub",
        default=None,
        help="WebSocket URL of the signaling hub (e.g. ws://1.2.3.4:8000). "
        "If set, this device holds a persistent connection there for "
        "presence/coordination, and /api/call runs the relay-first flow "
        "(relay through the hub, then hole-punch upgrade).",
    )
    args = parser.parse_args()
    bind_host = args.bind_host or args.host

    app = create_app(Path(args.data_dir), args.host, args.port, args.rendezvous, bind_host, hub_url=args.hub)
    identity = app.state.identity
    print(f"Device ID: {identity.device_id}")
    print(f"Advertising https://{args.host}:{args.port} (binding on {bind_host}) — self-signed cert, your browser will warn, that's expected")
    print(f"Pairing/confirm exchange runs over QUIC/UDP on the same port ({args.port})")
    if args.rendezvous:
        print(f"Registered with rendezvous at {args.rendezvous}")
    if args.hub:
        print(f"Signaling via hub at {args.hub} (relay-first calls enabled)")

    uvicorn.run(
        app,
        host=bind_host,
        port=args.port,
        ssl_certfile=str(identity.cert_path),
        ssl_keyfile=str(identity.key_path),
        log_level="info",
    )


if __name__ == "__main__":
    main()
