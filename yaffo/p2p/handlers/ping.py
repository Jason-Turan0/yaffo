class PingEndpoint:
    def __init__(self, service) -> None:
        self._service = service

    def send(self, peer_device_id: str, attempt_upgrade: bool = True) -> dict:
        """Ask a peer to prove reachability with the cheapest request."""
        return self._service.call(peer_device_id, payload={"type": "ping"}, attempt_upgrade=attempt_upgrade)

    def handle(self, body: dict) -> dict:
        return {"status": "ok", "type": "pong", "device_id": self._service.identity.device_id}
