import hashlib
import os
from pathlib import Path, PurePosixPath
from typing import Optional

from sqlalchemy import false, func, or_, select

from yaffo.db import db
from yaffo.db.models import (
    GRANT_SCOPE_ALBUM,
    GRANT_SCOPE_FOLDER,
    GRANT_SCOPE_MEDIA_DIR,
    AlbumItem,
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
from yaffo.p2p.messages import PeerRecord

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
    """Resolve a signed media-dir/scope BROWSE request (the listing target).

    Only listings name a path scope; pulling a file or a preview names a media item id
    (granted_item), so no attacker-supplied path reaches the filesystem there."""
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
    if not scope_is_granted(peer_device_id, media_dir_id, scope_path):
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
        # The id IS the handle: pulls and previews name it, and authorization is a
        # lookup against granted_media_query. The path travels as DATA — the requester
        # needs it for the destination layout and the resume sidecar — never as the
        # thing a request is keyed on.
        "media_item_id": item.id,
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


def granted_media_query(peer_device_id: str):
    """Every media item this peer's active grants cover — the authorization set, as a
    query.

    THIS IS THE ONE PLACE file access is decided. Listing pages this query (narrowed to
    the scope being browsed); pulling a file or a preview filters it to one id. Because
    the listing and the authorization are the same object, a file that cannot appear in
    a listing cannot be pulled either — the two cannot drift apart.

    Each grant contributes one clause:
      media_dir — every item under the dir's root;
      folder    — every item under the granted subtree;
      album     — exactly the album's members, wherever they live (an album grant
                  authorizes its membership, never the folder a member happens to be in).

    No grants means no items: the base query is false(), never "everything".
    """
    clauses = []
    for grant in p2p_repository.list_active_grants(db.session, peer_device_id):
        if grant.scope_type == GRANT_SCOPE_ALBUM and grant.album_id is not None:
            clauses.append(
                MediaItem.id.in_(
                    select(AlbumItem.media_item_id).where(AlbumItem.album_id == grant.album_id)
                )
            )
            continue

        media_dir = media_dir_repository.media_dir_by_id(db.session, grant.media_dir_id)
        if media_dir is None:
            continue  # a grant on a since-removed media dir is inert
        root = media_dir.path.expanduser().resolve()
        if grant.scope_type == GRANT_SCOPE_MEDIA_DIR:
            clauses.append(under_path(root))
        elif grant.scope_type == GRANT_SCOPE_FOLDER and grant.relative_path:
            target = (root / grant.relative_path.replace("/", os.sep)).resolve()
            if path_inside(target, root):
                clauses.append(under_path(target))

    if not clauses:
        return db.session.query(MediaItem).filter(false())
    return db.session.query(MediaItem).filter(or_(*clauses))


def granted_item(peer_device_id: str, media_item_id) -> Optional[MediaItem]:
    """The item, if this peer is granted it — the whole file-level check.

    Requests name a media item by ID, so there is no attacker-supplied path to
    sanitize: the serving device derives the path from its own row."""
    if not isinstance(media_item_id, int) or isinstance(media_item_id, bool):
        return None
    return granted_media_query(peer_device_id).filter(MediaItem.id == media_item_id).one_or_none()


def granted_album_ids(peer_device_id: str) -> list[int]:
    """Albums this peer holds an active grant on."""
    return [
        grant.album_id
        for grant in p2p_repository.list_active_grants(db.session, peer_device_id)
        if grant.scope_type == GRANT_SCOPE_ALBUM and grant.album_id is not None
    ]


def album_scope_query(album_id: int):
    """An album's members, resolved NOW: adding or removing photos changes what the peer
    sees on its next request, exactly as revocation does."""
    return (
        db.session.query(MediaItem)
        .join(AlbumItem, AlbumItem.media_item_id == MediaItem.id)
        .filter(AlbumItem.album_id == album_id)
    )


def scope_is_granted(peer_device_id: str, media_dir_id, scope_path: str, album_id=None) -> bool:
    """Whether the peer may BROWSE this scope.

    Distinct from granted_media_query, and deliberately weaker: it decides what the peer
    may point a listing at, while the files that listing yields are still intersected
    with the authorization set. So a scope can never widen what is actually served — at
    worst it names a target that produces nothing."""
    if album_id is not None:
        return album_id in granted_album_ids(peer_device_id)
    for grant in p2p_repository.list_active_grants(db.session, peer_device_id):
        if grant.media_dir_id != media_dir_id:
            continue
        if grant.scope_type == GRANT_SCOPE_MEDIA_DIR:
            return True
        if grant.scope_type == GRANT_SCOPE_FOLDER and grant.relative_path and scope_path:
            prefix = grant.relative_path.strip("/")
            if scope_path == prefix or scope_path.startswith(f"{prefix}/"):
                return True
    return False


def under_path(path: Path):
    """MediaItems stored at, or beneath, this path."""
    path_text = str(path).rstrip("/\\")
    return or_(
        MediaItem.full_file_path == path_text,
        MediaItem.full_file_path.like(f"{path_text}{os.sep}%"),
    )


def media_dir_location(path: Path, media_dirs) -> Optional[tuple[str, str]]:
    """(media_dir_id, relative_path) for a file — an album's members can live in
    different media dirs, so each manifest has to say which one it came from."""
    for entry in media_dirs:
        root = entry.path.expanduser().resolve()
        if path_inside(path, root):
            return entry.id, path.relative_to(root).as_posix()
    return None


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
