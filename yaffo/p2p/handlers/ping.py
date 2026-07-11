class PingHandler:
    def __init__(self, service) -> None:
        self._service = service

    def handle(self, body: dict) -> dict:
        return {"status": "ok", "type": "pong", "device_id": self._service.identity.device_id}
