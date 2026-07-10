from __future__ import annotations

import argparse
from contextlib import asynccontextmanager
from typing import Dict

from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from .relay import start_relay


def create_app(relay_bind_host: str = "0.0.0.0", relay_port: int = 40000) -> FastAPI:
    """The always-on hub: WebSocket signaling plus the UDP relay, one process.

    This is the successor to rendezvous.py's register/lookup pair, following
    the standard WebRTC/Tailscale shape: presence is simply "this device's
    WebSocket is open" (no address rows to go stale), and all call
    coordination is opaque JSON blobs forwarded between exactly the two
    devices they name. Like the rendezvous service before it, the hub plays
    NO role in the trust decision — it never sees keys or photo data, only
    device IDs, NAT-mapped addresses, and ciphertext datagrams; peers still
    authenticate each other end-to-end by certificate fingerprint pinning.

    In production terms this box is the design doc's TURN VM: the one
    component that must be publicly dialable and always on, so signaling
    lives here too instead of on scale-to-zero Cloud Run (a persistent
    WebSocket would defeat scale-to-zero anyway).
    """
    connected: Dict[str, WebSocket] = {}

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        transport, protocol = await start_relay(relay_bind_host, relay_port)
        app.state.relay = protocol
        try:
            yield
        finally:
            transport.close()

    app = FastAPI(title="Yaffo P2P Hub POC", lifespan=lifespan)
    app.state.connected = connected

    @app.get("/devices")
    def devices() -> dict:
        return {"connected": sorted(connected)}

    @app.websocket("/ws/{device_id}")
    async def signaling(websocket: WebSocket, device_id: str) -> None:
        await websocket.accept()
        connected[device_id] = websocket
        # The relay rides the same host the device dialed this WebSocket on,
        # so only the port needs announcing.
        await websocket.send_json({"type": "hub_info", "relay_port": relay_port})
        try:
            while True:
                message = await websocket.receive_json()
                target = message.get("to")
                message["from"] = device_id
                peer = connected.get(target)
                if peer is None:
                    await websocket.send_json(
                        {
                            "type": "error",
                            "token": message.get("token"),
                            "message": f"{target} is not connected to the hub",
                        }
                    )
                else:
                    await peer.send_json(message)
        except WebSocketDisconnect:
            pass
        finally:
            if connected.get(device_id) is websocket:
                del connected[device_id]

    return app


def main() -> None:
    import uvicorn

    parser = argparse.ArgumentParser(description="Signaling hub + UDP relay for the P2P POC")
    parser.add_argument("--host", default="127.0.0.1", help="Bind address for both the WebSocket/HTTP and relay sockets")
    parser.add_argument("--port", type=int, required=True, help="TCP port for WebSocket signaling")
    parser.add_argument("--relay-port", type=int, default=40000, help="UDP port for the datagram relay + STUN")
    args = parser.parse_args()

    print(f"Hub signaling on ws://{args.host}:{args.port}/ws/<device_id>, relay/STUN on udp:{args.relay_port}")
    uvicorn.run(create_app(args.host, args.relay_port), host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
