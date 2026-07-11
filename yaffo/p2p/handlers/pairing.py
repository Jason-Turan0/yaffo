import socket

from yaffo.db import db
from yaffo.db.repositories import p2p_repository
from yaffo.p2p.identity import device_id_from_pubkey_b64
from yaffo.p2p.messages import build_revocation_notice, verify_revocation_notice
from yaffo.p2p.pairing import PairingCode, PairingError, new_pairing_code, sign_nonce, verify_nonce_signature
from yaffo.p2p.errors import P2PServiceError


class PeeringEndpoint:
    def __init__(self, service) -> None:
        self._service = service

    def generate_pairing_code(self) -> PairingCode:
        service = self._service
        code = new_pairing_code(service.identity.device_id, service.identity.public_key_b64)
        with service._pending_lock:
            service._pending[code.nonce] = code
        return code

    def send_accept_pairing_code(self, code_text: str) -> dict:
        """Decode a pairing code, send the signed confirm, and store trust."""
        service = self._service
        code = PairingCode.decode(code_text)
        if code.is_expired():
            raise PairingError("pairing code expired")
        if code.device_id == service.identity.device_id:
            raise PairingError("cannot pair a device with itself")
        if device_id_from_pubkey_b64(code.pubkey) != code.device_id:
            raise PairingError("pairing code device id does not match its public key")

        payload = {
            "type": "pairing_confirm",
            "device_id": service.identity.device_id,
            "pubkey": service.identity.public_key_b64,
            "nonce": code.nonce,
            "signature": sign_nonce(service.identity.private_key, code.nonce),
        }
        report = service._submit(service._call_with_lan_first(code.device_id, payload=payload, attempt_upgrade=False))
        result = report["response"]

        with service._session() as session:
            p2p_repository.upsert_trusted_device(
                session, code.device_id, code.pubkey, result.get("display_name", code.device_id)
            )
        return {"peer_device_id": code.device_id, "via": report["path"]}

    def send_revoke_peer(self, peer_device_id: str) -> dict:
        """Revoke a paired peer and best-effort send a signed notice."""
        service = self._service
        with service._session() as session:
            known = p2p_repository.mark_device_revoked(session, peer_device_id)
        if not known:
            raise P2PServiceError(f"{peer_device_id} is not a known device")

        peer_notified = False
        if service.hub_connected:
            try:
                service._submit(
                    service._hub_client.notify_revoked(peer_device_id, build_revocation_notice(service.identity)),
                    timeout=5.0,
                )
                peer_notified = True
            except Exception:  # noqa: BLE001 — courtesy only, never blocks enforcement
                pass
        return {"revoked": peer_device_id, "peer_notified": peer_notified}

    def handle_pairing_confirm(self, body: dict) -> dict:
        """Verify a pairing nonce and record the joining device."""
        service = self._service
        with service._pending_lock:
            code = service._pending.pop(body.get("nonce"), None)
        if code is None:
            return {"status": "error", "detail": "unknown or already-used pairing code"}
        if code.is_expired():
            return {"status": "error", "detail": "pairing code expired"}
        pubkey = body.get("pubkey", "")
        device_id = body.get("device_id", "")
        if device_id_from_pubkey_b64(pubkey) != device_id:
            return {"status": "error", "detail": "device id does not match public key"}
        if not verify_nonce_signature(pubkey, body.get("nonce", ""), body.get("signature", "")):
            return {"status": "error", "detail": "signature verification failed"}

        with service._session():
            p2p_repository.upsert_trusted_device(db.session, device_id, pubkey, device_id)
        return {"status": "ok", "display_name": display_name(service)}

    def handle_revocation_notice(self, message: dict) -> None:
        """Honor only notices signed by a known trusted/revoked peer key."""
        service = self._service
        with service._session():
            if verify_revocation_notice(message, peer_lookup()) is None:
                p2p_repository.mark_device_revoked(db.session, message["device_id"])



def display_name(service) -> str:
    """What paired peers see this device as; the hostname is the best default."""
    try:
        return socket.gethostname() or service.identity.device_id
    except OSError:
        return service.identity.device_id


def peer_lookup():
    def lookup(device_id: str):
        row = p2p_repository.get_known_device(db.session, device_id)
        if row is None:
            return None
        from yaffo.p2p.messages import PeerRecord

        return PeerRecord(pubkey=row.pubkey, trust_state=row.trust_state)

    return lookup
