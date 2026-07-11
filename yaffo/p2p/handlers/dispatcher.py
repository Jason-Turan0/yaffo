def handle_stream_request(service, body: dict) -> dict:
    """Dispatch one authenticated QUIC stream request to its message handler."""
    kind = body.get("type")
    handlers = {
        "ping": service.ping.handle,
        "pairing_confirm": service.peering.handle_pairing_confirm,
        "list_shared": service.list_shared.handle,
        "list_files": service.list_files.handle,
        "pull_preview": service.pull_preview.handle,
        "pull_file": service.pull_file.handle,
    }
    handler = handlers.get(kind)
    if handler is None:
        return {"status": "error", "detail": f"unknown request type: {kind!r}"}
    return handler(body)
