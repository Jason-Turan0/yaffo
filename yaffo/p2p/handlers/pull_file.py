import base64
import hashlib
import os

from yaffo.db import db
from yaffo.db.models import MediaItem
from yaffo.db.repositories import media_dir_repository
from yaffo.p2p.handlers.sharing import (
    MAX_PULL_CHUNK_BYTES,
    clean_relative_path,
    grant_allows,
    parse_non_negative_int,
    path_inside,
    peer_lookup,
)
from yaffo.p2p.messages import verify_pull_file_request


class PullFileHandler:
    def __init__(self, service) -> None:
        self._service = service

    def handle(self, body: dict) -> dict:
        with self._service._session():
            denial = verify_pull_file_request(body, peer_lookup())
            if denial is not None:
                return {"status": "error", "detail": denial}

            peer_device_id = body["device_id"]
            media_dir_id = body.get("media_dir_id")
            try:
                relative_path = clean_relative_path(body.get("relative_path"))
                offset = parse_non_negative_int(body.get("offset"), "offset")
                requested_length = parse_non_negative_int(body.get("length"), "length")
            except ValueError as exc:
                return {"status": "error", "detail": str(exc)}
            length = min(requested_length, MAX_PULL_CHUNK_BYTES)
            if length == 0:
                return {"status": "error", "detail": "length must be greater than zero"}

            media_dir = media_dir_repository.media_dir_by_id(db.session, media_dir_id)
            if media_dir is None:
                return {"status": "error", "detail": "media directory is not configured"}
            root = media_dir.path.expanduser().resolve()
            path = (root / relative_path.replace("/", os.sep)).resolve()
            if not path_inside(path, root):
                return {"status": "error", "detail": "path escapes media directory"}
            if not grant_allows(peer_device_id, media_dir_id, relative_path):
                return {"status": "error", "detail": "no active share grant covers this file"}

            item = db.session.query(MediaItem).filter_by(full_file_path=str(path)).first()
            if item is None:
                return {"status": "error", "detail": "file is not indexed"}
            if not path.is_file():
                return {"status": "error", "detail": "file is not available"}

            size = path.stat().st_size
            if offset > size:
                return {"status": "error", "detail": "offset is beyond end of file"}
            with path.open("rb") as handle:
                handle.seek(offset)
                chunk = handle.read(length)
            next_offset = offset + len(chunk)

        return {
            "status": "ok",
            "type": "file_chunk",
            "device_id": self._service.identity.device_id,
            "media_dir_id": media_dir_id,
            "relative_path": relative_path,
            "offset": offset,
            "next_offset": next_offset,
            "size": size,
            "eof": next_offset >= size,
            "bytes": len(chunk),
            "chunk_sha256": hashlib.sha256(chunk).hexdigest(),
            "data_b64": base64.b64encode(chunk).decode("ascii"),
        }
