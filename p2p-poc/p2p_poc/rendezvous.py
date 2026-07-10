from __future__ import annotations

import argparse
from typing import Optional

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .rendezvous_store import InMemoryRegistryStore, RegistryStore


class RegisterRequest(BaseModel):
    device_id: str
    host: str
    port: int


def create_app(store: Optional[RegistryStore] = None) -> FastAPI:
    """The whole rendezvous service: a live map of device_id -> last-known

    address. Deliberately plain HTTP with no identity/auth of its own — per
    the design doc, this service only ever sees device IDs and addresses,
    never anything sensitive, and it plays no role in the trust decision
    (that's still fingerprint pinning + signature verification, unchanged).

    Defaults to an in-memory store (correct for local/test use — a single
    process). The deployed Cloud Run service passes a FirestoreRegistryStore
    instead; see cloud_run_rendezvous.py.
    """
    store = store or InMemoryRegistryStore()
    app = FastAPI(title="Yaffo P2P Rendezvous POC")
    app.state.store = store

    @app.post("/register")
    def register(body: RegisterRequest) -> dict:
        store.register(body.device_id, body.host, body.port)
        return {"status": "ok"}

    @app.get("/lookup/{device_id}")
    def lookup(device_id: str) -> JSONResponse:
        entry = store.lookup(device_id)
        if entry is None:
            return JSONResponse({"status": "error", "message": "device not currently reachable"}, status_code=404)
        return JSONResponse({"status": "ok", "host": entry["host"], "port": entry["port"]})

    return app


def main() -> None:
    import uvicorn

    parser = argparse.ArgumentParser(description="Minimal rendezvous/presence service for the pairing POC")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()

    print(f"Rendezvous service on http://{args.host}:{args.port} (plain HTTP — see module docstring for why)")
    uvicorn.run(create_app(), host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
