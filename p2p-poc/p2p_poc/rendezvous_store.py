from __future__ import annotations

import time
from typing import Optional, Protocol

PRESENCE_TTL_SECONDS = 120  # a device that hasn't re-registered in this long is considered unreachable


class RegistryStore(Protocol):
    def register(self, device_id: str, host: str, port: int) -> None: ...

    def lookup(self, device_id: str) -> Optional[dict]: ...


class InMemoryRegistryStore:
    """Default backend: a plain dict. Fine for a single process — which is

    exactly the local/test topology (loopback devices + this service all on
    one machine), but wrong for Cloud Run: multiple instances (or a cold
    start after scale-to-zero) wouldn't share this dict, so a register() on
    one instance could be invisible to a lookup() on another. Not used when
    deployed — see FirestoreRegistryStore.
    """

    def __init__(self) -> None:
        self._data: dict[str, dict] = {}

    def register(self, device_id: str, host: str, port: int) -> None:
        self._data[device_id] = {"host": host, "port": port, "registered_at": time.time()}

    def lookup(self, device_id: str) -> Optional[dict]:
        entry = self._data.get(device_id)
        if entry is None or time.time() - entry["registered_at"] > PRESENCE_TTL_SECONDS:
            return None
        return entry


class FirestoreRegistryStore:
    """Cloud Run backend: state lives in Firestore instead of process memory,

    so it's correct regardless of how many container instances are running
    or whether one just cold-started. Only ever stores device_id -> address —
    never anything from the trust/pairing layer, matching the design doc.
    """

    def __init__(self, collection: str = "p2p_poc_rendezvous") -> None:
        from google.cloud import firestore  # imported lazily so local/test runs never need this dependency

        self._collection = firestore.Client().collection(collection)

    def register(self, device_id: str, host: str, port: int) -> None:
        self._collection.document(device_id).set({"host": host, "port": port, "registered_at": time.time()})

    def lookup(self, device_id: str) -> Optional[dict]:
        doc = self._collection.document(device_id).get()
        if not doc.exists:
            return None
        entry = doc.to_dict()
        if time.time() - entry["registered_at"] > PRESENCE_TTL_SECONDS:
            return None
        return entry
