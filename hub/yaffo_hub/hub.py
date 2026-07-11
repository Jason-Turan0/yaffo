from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from typing import Dict, Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from .auth import new_challenge_nonce, verify_auth
from .config import HubSettings
from .limits import RateLimiter
from .relay import RelayLimits, start_relay, valid_token

logger = logging.getLogger("yaffo_hub")

# WebSocket close codes (4xxx = application-defined).
CLOSE_RATE_LIMITED = 4429
CLOSE_AUTH_TIMEOUT = 4401
CLOSE_AUTH_FAILED = 4403
CLOSE_REPLACED = 4409

# Message types whose brokered relay token gets allowlisted as they are
# forwarded. connect_request additionally counts against the sender's
# concurrent-session cap (the caller is the one opening sessions).
_BROKERING_TYPES = ("connect_request", "connect_response")


def create_app(settings: Optional[HubSettings] = None) -> FastAPI:
    """The always-on hub: authenticated WebSocket signaling plus the UDP
    relay (+ STUN), one process — the productionized POC hub.

    Presence is simply "this device's WebSocket is open" (no address rows to
    go stale), and all call coordination is opaque JSON blobs forwarded
    between exactly the two devices they name. The hub plays NO role in any
    trust decision — it never sees keys or photo data, only device IDs,
    NAT-mapped addresses, and ciphertext datagrams; peers authenticate each
    other end-to-end by certificate fingerprint pinning.

    Hardening relative to the POC: challenge-response connect auth (so
    device IDs can't be squatted or spoofed — IDs are self-authenticating
    hashes of the presented pubkey), relay sessions only for hub-brokered
    tokens, and TTL/byte/session/rate limits throughout.
    """
    settings = settings or HubSettings()
    connected: Dict[str, WebSocket] = {}
    # device_id -> {token: time authorized}; prunes against the relay's view.
    brokered: Dict[str, Dict[str, float]] = {}
    connect_limiter = RateLimiter(settings.connects_per_minute_per_ip, 60.0)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        relay_limits = RelayLimits(
            token_ttl_seconds=settings.token_ttl_seconds,
            session_ttl_seconds=settings.session_ttl_seconds,
            max_session_bytes=settings.max_session_bytes,
        )
        transport, protocol = await start_relay(settings.relay_host, settings.relay_port, relay_limits)
        app.state.relay = protocol
        try:
            yield
        finally:
            transport.close()

    app = FastAPI(title="Yaffo P2P Hub", lifespan=lifespan)
    app.state.connected = connected
    app.state.settings = settings

    @app.get("/healthz")
    def healthz() -> dict:
        return {
            "status": "ok",
            "connected_devices": len(connected),
            "relay": app.state.relay.stats(),
        }

    @app.get("/devices")
    def devices() -> dict:
        return {"connected": sorted(connected)}

    def _over_session_cap(device_id: str, token: str) -> bool:
        tokens = brokered.setdefault(device_id, {})
        for known in list(tokens):
            # A token stops counting the moment the relay has forgotten it:
            # session closed by a BYE, pruned after its idle TTL, or never
            # HELLO'd and the allowlist entry expired. (authorize() runs
            # before the token is recorded here, so a counted token is
            # is_active until one of those genuinely happens.)
            if not app.state.relay.is_active(known):
                del tokens[known]
        if token in tokens:
            return False  # re-announcing an existing session is not a new one
        return len(tokens) >= settings.max_sessions_per_device

    async def _authenticate(websocket: WebSocket, device_id: str) -> bool:
        nonce = new_challenge_nonce()
        await websocket.send_json({"type": "challenge", "nonce": nonce})
        try:
            message = await asyncio.wait_for(
                websocket.receive_json(), timeout=settings.auth_timeout_seconds
            )
        except asyncio.TimeoutError:
            await websocket.close(code=CLOSE_AUTH_TIMEOUT)
            return False
        except (ValueError, KeyError):
            await websocket.close(code=CLOSE_AUTH_FAILED)
            return False
        if (
            not isinstance(message, dict)
            or message.get("type") != "auth"
            or not verify_auth(
                device_id,
                str(message.get("pubkey", "")),
                nonce,
                str(message.get("signature", "")),
            )
        ):
            logger.info("event=auth_failed device_id=%s", device_id)
            await websocket.close(code=CLOSE_AUTH_FAILED)
            return False
        return True

    @app.websocket("/ws/{device_id}")
    async def signaling(websocket: WebSocket, device_id: str) -> None:
        client_ip = websocket.client.host if websocket.client else "unknown"
        await websocket.accept()
        if not connect_limiter.allow(client_ip):
            logger.info("event=connect_rate_limited ip=%s", client_ip)
            await websocket.close(code=CLOSE_RATE_LIMITED)
            return

        try:
            if not await _authenticate(websocket, device_id):
                return
        except WebSocketDisconnect:
            return

        previous = connected.get(device_id)
        connected[device_id] = websocket
        if previous is not None:
            # A reconnect (e.g. after a network blip) supersedes the stale
            # socket; only an *authenticated* holder of the key can do this.
            try:
                await previous.close(code=CLOSE_REPLACED)
            except RuntimeError:
                pass  # already closing on its own
        logger.info("event=device_connected device_id=%s ip=%s", device_id, client_ip)

        # The relay rides the same host the device dialed this WebSocket on,
        # so only the port needs announcing.
        await websocket.send_json({"type": "hub_info", "relay_port": settings.relay_port})
        try:
            while True:
                message = await websocket.receive_json()
                if not isinstance(message, dict):
                    continue
                target = message.get("to")
                message["from"] = device_id

                token = message.get("token")
                brokering = message.get("type") in _BROKERING_TYPES and isinstance(token, str)
                if brokering:
                    if not valid_token(token):
                        await websocket.send_json(
                            {"type": "error", "token": token, "message": "malformed relay token"}
                        )
                        continue
                    if message["type"] == "connect_request" and _over_session_cap(device_id, token):
                        logger.info("event=session_cap_hit device_id=%s", device_id)
                        await websocket.send_json(
                            {"type": "error", "token": token, "message": "too many active relay sessions"}
                        )
                        continue

                peer = connected.get(target)
                if peer is None:
                    await websocket.send_json(
                        {
                            "type": "error",
                            "token": message.get("token"),
                            "message": f"{target} is not connected to the hub",
                        }
                    )
                    continue
                if brokering:
                    # Only forwarded brokering messages authorize (and count)
                    # a token — a call that dies here (peer offline) must not
                    # eat a session slot.
                    app.state.relay.authorize(token)
                    brokered.setdefault(device_id, {})[token] = time.monotonic()
                await peer.send_json(message)
        except WebSocketDisconnect:
            pass
        except (ValueError, KeyError) as exc:
            logger.warning("event=malformed_message device_id=%s error=%s", device_id, exc)
        finally:
            if connected.get(device_id) is websocket:
                del connected[device_id]
                brokered.pop(device_id, None)
            logger.info("event=device_disconnected device_id=%s", device_id)

    return app
