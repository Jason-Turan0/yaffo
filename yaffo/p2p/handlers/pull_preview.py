import base64
from pathlib import Path

from yaffo.db import db
from yaffo.db.models import MEDIA_TYPE_VIDEO, MediaItem
from yaffo.logging_config import get_logger
from yaffo.p2p.handlers.sharing import (
    DEFAULT_PREVIEW_DIMENSION,
    MAX_PREVIEW_DIMENSION,
    parse_non_negative_int,
    peer_lookup,
    resolve_scoped_request,
)
from yaffo.p2p.messages import verify_pull_preview_request
from yaffo.utils.image import preview_jpeg_bytes

logger = get_logger(__name__, "webapp")


class PullPreviewHandler:
    def __init__(self, service) -> None:
        self._service = service

    def handle(self, body: dict) -> dict:
        """Return a downscaled JPEG preview for one granted indexed file."""
        with self._service._session():
            denial = verify_pull_preview_request(body, peer_lookup())
            if denial is not None:
                return {"status": "error", "detail": denial}
            error, media_dir_id, relative_path, root, target = resolve_scoped_request(body)
            if error is not None:
                return error
            if not relative_path:
                return {"status": "error", "detail": "relative_path is required"}
            try:
                max_dimension = parse_non_negative_int(body.get("max_dimension"), "max_dimension")
            except ValueError as exc:
                return {"status": "error", "detail": str(exc)}
            max_dimension = min(max_dimension or DEFAULT_PREVIEW_DIMENSION, MAX_PREVIEW_DIMENSION)

            item = db.session.query(MediaItem).filter_by(full_file_path=str(target)).first()
            if item is None:
                return {"status": "error", "detail": "file is not indexed"}
            source = target
            if item.media_type == MEDIA_TYPE_VIDEO:
                if not item.poster_path:
                    return {"status": "error", "detail": "video has no preview"}
                source = Path(item.poster_path).expanduser().resolve()
            if not source.is_file():
                return {"status": "error", "detail": "file is not available"}
            try:
                data = preview_jpeg_bytes(source, max_dimension)
            except Exception:  # noqa: BLE001 — unreadable/corrupt image, codec gaps
                logger.exception("preview generation failed for %s", target)
                return {"status": "error", "detail": "could not generate a preview"}

        return {
            "status": "ok",
            "type": "file_preview",
            "device_id": self._service.identity.device_id,
            "media_dir_id": media_dir_id,
            "relative_path": relative_path,
            "max_dimension": max_dimension,
            "data_b64": base64.b64encode(data).decode("ascii"),
        }
