import base64
import hashlib
import os
from pathlib import Path
from typing import Optional

from yaffo.db import db
from yaffo.db.models import MediaItem
from yaffo.db.repositories import media_dir_repository
from yaffo.p2p.errors import P2PServiceError
from yaffo.p2p.handlers.sharing import (
    DEFAULT_PULL_CHUNK_BYTES,
    MAX_PULL_CHUNK_BYTES,
    clean_destination_path,
    clean_relative_path,
    grant_allows,
    parse_non_negative_int,
    path_inside,
    peer_lookup,
    relative_path_inside_scope,
    safe_path_component,
    sha256_file,
)
from yaffo.p2p.messages import build_pull_file_request, verify_pull_file_request


class PullFileEndpoint:
    def __init__(self, service) -> None:
        self._service = service

    def send(
        self,
        peer_device_id: str,
        media_dir_id: str,
        relative_path: str,
        offset: int = 0,
        length: int = DEFAULT_PULL_CHUNK_BYTES,
    ) -> dict:
        """Pull one bounded file chunk from a trusted peer."""
        payload = build_pull_file_request(self._service.identity, media_dir_id, relative_path, offset, length)
        return self._expect_ok(self._service.call(peer_device_id, payload=payload, attempt_upgrade=False)["response"])

    def download(
        self,
        peer_device_id: str,
        media_dir_id: str,
        relative_path: str,
        destination_root: Path,
        expected_sha256: Optional[str] = None,
        chunk_size: int = DEFAULT_PULL_CHUNK_BYTES,
        destination_device_name: Optional[str] = None,
        destination_collection_path: Optional[str] = None,
        source_scope_path: Optional[str] = None,
    ) -> dict:
        """Pull one remote file into the configured download directory."""
        clean_path = clean_relative_path(relative_path)
        root = destination_root.expanduser().resolve()
        device_folder = safe_path_component(destination_device_name or peer_device_id, peer_device_id)
        collection_folder = clean_destination_path(destination_collection_path or media_dir_id)
        file_path = relative_path_inside_scope(clean_path, source_scope_path)
        destination = (
            root
            / device_folder
            / collection_folder.replace("/", os.sep)
            / file_path.replace("/", os.sep)
        ).resolve()
        if not path_inside(destination, root):
            raise P2PServiceError("destination path escapes the download directory")
        destination.parent.mkdir(parents=True, exist_ok=True)
        partial = destination.with_name(f"{destination.name}.partial")

        offset = partial.stat().st_size if partial.exists() else 0
        size = None
        with partial.open("ab") as handle:
            while True:
                chunk = self.send(
                    peer_device_id,
                    media_dir_id,
                    clean_path,
                    offset=offset,
                    length=chunk_size,
                )
                data = base64.b64decode(chunk["data_b64"])
                if chunk.get("media_dir_id") != media_dir_id or chunk.get("relative_path") != clean_path:
                    raise P2PServiceError("peer returned a chunk for a different file")
                if chunk.get("offset") != offset:
                    raise P2PServiceError("peer returned a chunk at the wrong offset")
                if len(data) != chunk.get("bytes"):
                    raise P2PServiceError("peer returned a chunk with the wrong byte count")
                if hashlib.sha256(data).hexdigest() != chunk.get("chunk_sha256"):
                    raise P2PServiceError("peer returned a chunk with the wrong checksum")
                if not data and not chunk.get("eof"):
                    raise P2PServiceError("peer returned an empty chunk before the end of the file")

                size = chunk["size"]
                handle.write(data)
                offset = chunk["next_offset"]
                if chunk.get("eof"):
                    break

        actual_sha256 = sha256_file(partial)
        if expected_sha256 and actual_sha256 != expected_sha256:
            raise P2PServiceError("downloaded file checksum did not match the peer manifest")
        partial.replace(destination)
        return {
            "saved_to": str(destination),
            "relative_path": str(destination.relative_to(root)),
            "bytes": size if size is not None else offset,
            "sha256": actual_sha256,
        }

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

    def _expect_ok(self, response: dict) -> dict:
        if not isinstance(response, dict) or response.get("status") != "ok":
            detail = response.get("detail", "peer reported an error") if isinstance(response, dict) else "no response"
            raise P2PServiceError(detail)
        return response
