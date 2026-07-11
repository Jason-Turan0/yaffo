def handle_stream_request(service, body: dict) -> dict:
    """Dispatch one authenticated QUIC stream request to its message handler."""
    kind = body.get("type")
    handlers = {
        "ping": service.ping.handle,
        "pairing_confirm": service.peering.handle_pairing_confirm,
        "list_shared": service.sharing.handle_list_shared,
        "list_files": service.sharing.handle_list_files,
        "pull_preview": service.sharing.handle_pull_preview,
        "pull_file": service.sharing.handle_pull_file,
    }
    handler = handlers.get(kind)
    if handler is None:
        return {"status": "error", "detail": f"unknown request type: {kind!r}"}
    return handler(body)
