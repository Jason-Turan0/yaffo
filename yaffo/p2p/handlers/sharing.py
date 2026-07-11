import base64
import hashlib
import os
from pathlib import Path, PurePosixPath
from typing import Optional

from sqlalchemy import func, or_

from yaffo.db import db
from yaffo.db.models import (
    GRANT_SCOPE_FOLDER,
    GRANT_SCOPE_MEDIA_DIR,
    MEDIA_TYPE_PHOTO,
    MEDIA_TYPE_VIDEO,
    ClassificationLabel,
    Face,
    MediaItem,
    MediaLabel,
    Person,
    PersonFace,
    Tag,
)
from yaffo.db.repositories import media_dir_repository, p2p_repository
from yaffo.db.repositories.media_filter_repository import apply_media_filters
from yaffo.p2p.errors import P2PServiceError
from yaffo.p2p.messages import (
    PeerRecord,
    build_pull_file_request,
    build_pull_preview_request,
)

DEFAULT_PULL_CHUNK_BYTES = 1024 * 1024
MAX_PULL_CHUNK_BYTES = 4 * 1024 * 1024
DEFAULT_LIST_FILES_LIMIT = 50
MAX_LIST_FILES_LIMIT = 200
DEFAULT_PREVIEW_DIMENSION = 512
MAX_PREVIEW_DIMENSION = 1024


def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _is_int_list(value) -> bool:
    return isinstance(value, list) and all(_is_int(item) for item in value)


def _is_str_list(value) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


LIST_FILES_FILTER_VALIDATORS = {
    "path": lambda v: isinstance(v, str),
    "media_type": lambda v: v in (MEDIA_TYPE_PHOTO, MEDIA_TYPE_VIDEO),
    "year": _is_int,
    "month": _is_int,
    "device": lambda v: isinstance(v, str),
    "favorite": lambda v: isinstance(v, (bool, int)),
    "gender": _is_int,
    "people": _is_int_list,
    "person_match_type": lambda v: v in ("any", "all"),
    "labels": _is_int_list,
    "labels_match_type": lambda v: v in ("any", "all"),
    "tag_name": lambda v: isinstance(v, str),
    "tag_value": lambda v: isinstance(v, str),
    "locations": _is_str_list,
    "location_match_type": lambda v: v in ("any", "all"),
    "unnamed": lambda v: isinstance(v, (bool, int)),
    "proximity_lat": _is_number,
    "proximity_lon": _is_number,
    "proximity_km": _is_number,
}
LIST_FILES_FILTER_KEYS = tuple(LIST_FILES_FILTER_VALIDATORS)


def peer_lookup():
    def lookup(device_id: str) -> Optional[PeerRecord]:
        row = p2p_repository.get_known_device(db.session, device_id)
        return PeerRecord(pubkey=row.pubkey, trust_state=row.trust_state) if row else None

    return lookup


def resolve_scoped_request(body: dict):
    """Resolve a signed media-dir/scope request and check an active grant."""
    peer_device_id = body["device_id"]
    media_dir_id = body.get("media_dir_id")
    raw_scope = body.get("relative_path") or ""
    if not isinstance(raw_scope, str):
        return {"status": "error", "detail": "relative_path must be a string"}, None, None, None, None
    try:
        scope_path = clean_relative_path(raw_scope) if raw_scope.strip() else ""
    except ValueError as exc:
        return {"status": "error", "detail": str(exc)}, None, None, None, None
    media_dir = media_dir_repository.media_dir_by_id(db.session, media_dir_id)
    if media_dir is None:
        return {"status": "error", "detail": "media directory is not configured"}, None, None, None, None
    root = media_dir.path.expanduser().resolve()
    target = (root / scope_path.replace("/", os.sep)).resolve() if scope_path else root
    if not path_inside(target, root):
        return {"status": "error", "detail": "path escapes media directory"}, None, None, None, None
    if not grant_allows(peer_device_id, media_dir_id, scope_path):
        return {"status": "error", "detail": "no active share grant covers this scope"}, None, None, None, None
    return None, media_dir_id, scope_path, root, target


def validate_list_filters(filters: dict) -> Optional[dict]:
    if not isinstance(filters, dict):
        return {"status": "error", "detail": "filters must be an object"}
    unknown = set(filters) - set(LIST_FILES_FILTER_KEYS)
    if unknown:
        return {"status": "error", "detail": f"unknown filters: {', '.join(sorted(unknown))}"}
    for key, value in filters.items():
        if not LIST_FILES_FILTER_VALIDATORS[key](value):
            return {"status": "error", "detail": f"invalid value for filter {key!r}"}
    return None


def apply_list_filters(files_query, target: Path, filters: dict):
    path_text = (filters.get("path") or "").strip()
    if path_text:
        escaped = path_text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        relative_expr = func.substr(MediaItem.full_file_path, len(str(target).rstrip("/\\")) + 2)
        files_query = files_query.filter(relative_expr.like(f"%{escaped}%", escape="\\"))
    return apply_media_filters(
        db.session,
        files_query,
        {
            "media_type": filters.get("media_type"),
            "year": filters.get("year"),
            "month": filters.get("month"),
            "device": (filters.get("device") or "").strip() or None,
            "favorite": filters.get("favorite"),
            "person_ids": filters.get("people"),
            "person_match_type": filters.get("person_match_type", "any"),
            "gender": filters.get("gender"),
            "label_ids": filters.get("labels"),
            "labels_match_type": filters.get("labels_match_type", "any"),
            "tag_name": filters.get("tag_name"),
            "tag_value": filters.get("tag_value"),
            "location_names": filters.get("locations"),
            "location_match_type": filters.get("location_match_type", "any"),
            "unnamed": filters.get("unnamed"),
            "proximity_lat": filters.get("proximity_lat"),
            "proximity_lon": filters.get("proximity_lon"),
            "proximity_km": filters.get("proximity_km"),
        },
    )


def list_files_facets(scope_query, filters: dict) -> dict:
    scope_ids = scope_query.with_entities(MediaItem.id)
    facets = {
        "years": [
            row[0]
            for row in scope_query.with_entities(MediaItem.year)
            .filter(MediaItem.year.isnot(None))
            .distinct()
            .order_by(MediaItem.year)
        ],
        "devices": [
            row[0]
            for row in scope_query.with_entities(MediaItem.device)
            .filter(MediaItem.device.isnot(None), MediaItem.device != "")
            .distinct()
            .order_by(MediaItem.device)
        ],
        "people": [
            {"id": person_id, "name": name}
            for person_id, name in db.session.query(Person.id, Person.name)
            .join(PersonFace, PersonFace.person_id == Person.id)
            .join(Face, Face.id == PersonFace.face_id)
            .filter(Face.media_item_id.in_(scope_ids), Person.name.isnot(None))
            .distinct()
            .order_by(Person.name)
        ],
        "labels": [
            {"id": label_id, "name": name}
            for label_id, name in db.session.query(ClassificationLabel.id, ClassificationLabel.name)
            .join(MediaLabel, MediaLabel.label_id == ClassificationLabel.id)
            .filter(MediaLabel.media_item_id.in_(scope_ids))
            .distinct()
            .order_by(func.lower(ClassificationLabel.name))
        ],
        "tag_names": [
            row[0]
            for row in db.session.query(Tag.tag_name)
            .filter(Tag.media_item_id.in_(scope_ids), Tag.tag_name.isnot(None))
            .distinct()
            .order_by(Tag.tag_name)
        ],
        "locations": [
            row[0]
            for row in scope_query.with_entities(MediaItem.location_name)
            .filter(MediaItem.location_name.isnot(None), MediaItem.location_name != "")
            .distinct()
            .order_by(MediaItem.location_name)
        ],
    }
    if filters.get("tag_name"):
        facets["tag_values"] = [
            row[0]
            for row in db.session.query(Tag.tag_value)
            .filter(
                Tag.media_item_id.in_(scope_ids),
                Tag.tag_name == filters["tag_name"],
                Tag.tag_value.isnot(None),
            )
            .distinct()
            .order_by(Tag.tag_value)
        ]
    return facets


def grant_target(root: Path, grant) -> Optional[Path]:
    if grant.scope_type == GRANT_SCOPE_MEDIA_DIR:
        return root
    if grant.scope_type == GRANT_SCOPE_FOLDER and grant.relative_path:
        target = (root / grant.relative_path.replace("/", os.sep)).resolve()
        return target if path_inside(target, root) else None
    return None


def indexed_media_query(path: Path):
    path_text = str(path).rstrip("/\\")
    under = f"{path_text}{os.sep}%"
    return db.session.query(MediaItem).filter(
        or_(MediaItem.full_file_path == path_text, MediaItem.full_file_path.like(under))
    )


def file_manifest(media_dir_id: str, relative_path: str, path: Path, item: MediaItem) -> dict:
    stat = path.stat()
    return {
        "media_dir_id": media_dir_id,
        "relative_path": relative_path,
        "name": path.name,
        "size": stat.st_size,
        "mtime": stat.st_mtime,
        "media_type": item.media_type,
        "date_taken": item.date_taken,
        "location_name": item.location_name,
        "duration_seconds": item.duration_seconds,
    }


def grant_allows(peer_device_id: str, media_dir_id: str, relative_path: str) -> bool:
    """Whether an active grant covers this file or browsed scope."""
    for grant in p2p_repository.list_active_grants(db.session, peer_device_id):
        if grant.media_dir_id != media_dir_id:
            continue
        if grant.scope_type == GRANT_SCOPE_MEDIA_DIR:
            return True
        if grant.scope_type == GRANT_SCOPE_FOLDER and grant.relative_path and relative_path:
            prefix = grant.relative_path.strip("/")
            if relative_path == prefix or relative_path.startswith(f"{prefix}/"):
                return True
    return False


def clean_relative_path(raw_path) -> str:
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise ValueError("relative_path is required")
    path = PurePosixPath(raw_path.strip())
    if path.is_absolute() or any(part in ("", ".", "..") for part in path.parts):
        raise ValueError("relative_path must stay inside the media directory")
    return path.as_posix()


def safe_path_component(raw_value: str, fallback: str) -> str:
    value = (raw_value or "").strip() or fallback
    cleaned = "".join("_" if ch in '<>:"/\\|?*' or ord(ch) < 32 else ch for ch in value).strip()
    if cleaned in ("", ".", ".."):
        return fallback
    return cleaned


def clean_destination_path(raw_path: str) -> str:
    path = PurePosixPath((raw_path or "").strip())
    if path.is_absolute():
        raise ValueError("destination collection must be relative")
    parts = [safe_path_component(part, "Files") for part in path.parts if part not in ("", ".", "..")]
    return PurePosixPath(*parts).as_posix() if parts else "Files"


def relative_path_inside_scope(relative_path: str, scope_path: Optional[str]) -> str:
    if not scope_path:
        return relative_path
    scope = clean_relative_path(scope_path).rstrip("/")
    if relative_path == scope:
        return PurePosixPath(relative_path).name
    prefix = f"{scope}/"
    if relative_path.startswith(prefix):
        return relative_path[len(prefix):]
    return PurePosixPath(relative_path).name


def parse_non_negative_int(raw_value, field: str) -> int:
    if not isinstance(raw_value, int) or raw_value < 0:
        raise ValueError(f"{field} must be a non-negative integer")
    return raw_value


def path_inside(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class SharingEndpoint:
    def __init__(self, service) -> None:
        self._service = service
        from yaffo.p2p.handlers.list_files import ListFilesEndpoint
        from yaffo.p2p.handlers.list_shared import ListSharedEndpoint
        from yaffo.p2p.handlers.pull_file import PullFileHandler
        from yaffo.p2p.handlers.pull_preview import PullPreviewHandler

        self.list_shared = ListSharedEndpoint(service)
        self.list_files = ListFilesEndpoint(service)
        self._pull_preview_handler = PullPreviewHandler(service)
        self._pull_file_handler = PullFileHandler(service)

    def list_shared_files(
        self,
        peer_device_id: str,
        media_dir_id: str,
        relative_path: str = "",
        filters: Optional[dict] = None,
        offset: int = 0,
        limit: int = DEFAULT_LIST_FILES_LIMIT,
    ) -> dict:
        return self.list_files.send(peer_device_id, media_dir_id, relative_path, filters, offset, limit)

    def pull_preview(
        self,
        peer_device_id: str,
        media_dir_id: str,
        relative_path: str,
        max_dimension: int = DEFAULT_PREVIEW_DIMENSION,
    ) -> bytes:
        """Fetch a downscaled JPEG preview for one shared file."""
        payload = build_pull_preview_request(self._service.identity, media_dir_id, relative_path, max_dimension)
        response = self._expect_ok(self._service.call(peer_device_id, payload=payload, attempt_upgrade=False)["response"])
        return base64.b64decode(response["data_b64"])

    def pull_file_chunk(
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

    def pull_file(
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
                chunk = self.pull_file_chunk(
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

    def handle_list_shared(self, body: dict) -> dict:
        return self.list_shared.handle(body)

    def handle_list_files(self, body: dict) -> dict:
        return self.list_files.handle(body)

    def handle_pull_preview(self, body: dict) -> dict:
        return self._pull_preview_handler.handle(body)

    def handle_pull_file(self, body: dict) -> dict:
        return self._pull_file_handler.handle(body)

    def _expect_ok(self, response: dict) -> dict:
        if not isinstance(response, dict) or response.get("status") != "ok":
            detail = response.get("detail", "peer reported an error") if isinstance(response, dict) else "no response"
            raise P2PServiceError(detail)
        return response
