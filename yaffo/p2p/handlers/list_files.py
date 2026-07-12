from pathlib import Path
from typing import Optional

from yaffo.db import db
from yaffo.db.models import MediaItem
from yaffo.db.repositories import media_dir_repository
from yaffo.p2p.handlers.sharing import (
    DEFAULT_LIST_FILES_LIMIT,
    MAX_LIST_FILES_LIMIT,
    album_scope_query,
    apply_list_filters,
    file_manifest,
    granted_album_ids,
    granted_media_query,
    indexed_media_query,
    list_files_facets,
    media_dir_location,
    path_inside,
    peer_lookup,
    resolve_scoped_request,
    parse_non_negative_int,
    validate_list_filters,
)
from yaffo.p2p.identity import DeviceIdentity
from yaffo.p2p.messages import PeerLookup, build_signed_message, verify_signed_message

MESSAGE_LIST_FILES = "list_files"


def build_list_files_request(
    identity: DeviceIdentity,
    media_dir_id: str,
    relative_path: str,
    filters: dict,
    offset: int,
    limit: int,
    album_id: Optional[int] = None,
) -> dict:
    """One page of a scope's file manifests.

    A scope is either a PATH — `media_dir_id` plus `relative_path` ("" for a whole
    media dir) — or an ALBUM (`album_id`), whose members are a set of items that can
    live in different media dirs. `filters` is a flat dict of optional criteria the
    serving side applies *after* scoping to the grant."""
    return build_signed_message(
        identity,
        MESSAGE_LIST_FILES,
        {
            "media_dir_id": media_dir_id,
            "relative_path": relative_path,
            "album_id": album_id,
            "filters": filters,
            "offset": offset,
            "limit": limit,
        },
    )


def verify_list_files_request(body: dict, lookup: PeerLookup) -> Optional[str]:
    return verify_signed_message(
        body,
        lookup,
        MESSAGE_LIST_FILES,
        signed_fields=("media_dir_id", "relative_path", "album_id", "filters", "offset", "limit"),
    )


class ListFilesEndpoint:
    def __init__(self, service) -> None:
        self._service = service

    def send(
        self,
        peer_device_id: str,
        media_dir_id: str = "",
        relative_path: str = "",
        filters: Optional[dict] = None,
        offset: int = 0,
        limit: int = DEFAULT_LIST_FILES_LIMIT,
        album_id: Optional[int] = None,
    ) -> dict:
        """Fetch one page of manifests for a granted scope on a peer."""
        payload = build_list_files_request(
            self._service.identity,
            media_dir_id,
            relative_path,
            filters or {},
            offset,
            limit,
            album_id=album_id,
        )
        response = self._service.call(peer_device_id, payload=payload, attempt_upgrade=False)["response"]
        return self._service.expect_ok(response)

    def handle(self, body: dict) -> dict:
        """Return one page of granted file manifests plus scope-constrained facets."""
        with self._service._session():
            denial = verify_list_files_request(body, peer_lookup())
            if denial is not None:
                return {"status": "error", "detail": denial}

            album_id = body.get("album_id")
            if album_id is not None:
                return self._handle_album(body, album_id)

            error, media_dir_id, scope_path, root, target = resolve_scoped_request(body)
            if error is not None:
                return error
            try:
                offset, limit = _paging(body)
            except ValueError as exc:
                return {"status": "error", "detail": str(exc)}
            filters = body.get("filters") or {}
            filter_error = validate_list_filters(filters)
            if filter_error is not None:
                return filter_error

            # The scope is what the peer asked to browse; the authorization set is what
            # it may see. Serve the intersection, so a listing can never widen a grant.
            scope_query = _granted(body["device_id"], indexed_media_query(target))
            files_query = apply_list_filters(scope_query, target, filters)
            total = files_query.count()
            files = []
            for item in _page(files_query, offset, limit):
                path = Path(item.full_file_path).expanduser().resolve()
                if not path_inside(path, root) or not path.is_file():
                    continue
                files.append(file_manifest(media_dir_id, path.relative_to(root).as_posix(), path, item))
            facets = list_files_facets(scope_query, filters)

        return {
            "status": "ok",
            "type": "shared_files",
            "device_id": self._service.identity.device_id,
            "media_dir_id": media_dir_id,
            "relative_path": scope_path,
            "album_id": None,
            "filters": filters,
            "offset": offset,
            "limit": limit,
            "total": total,
            "files": files,
            "facets": facets,
        }

    def _handle_album(self, body: dict, album_id) -> dict:
        """One page of an ALBUM grant's members.

        Membership is resolved at request time, so removing a photo from the album
        drops it from the peer's next listing — the serving device re-decides on every
        request, exactly as it does for revocation. Each manifest names the media dir
        the file actually came from (an album's members can span several), and a member
        outside every configured media dir is skipped rather than served from nowhere.
        """
        if not isinstance(album_id, int) or isinstance(album_id, bool):
            return {"status": "error", "detail": "album_id must be an integer"}
        peer_device_id = body["device_id"]
        if album_id not in granted_album_ids(peer_device_id):
            return {"status": "error", "detail": "no active share grant covers this album"}
        try:
            offset, limit = _paging(body)
        except ValueError as exc:
            return {"status": "error", "detail": str(exc)}
        filters = body.get("filters") or {}
        filter_error = validate_list_filters(filters)
        if filter_error is not None:
            return filter_error

        media_dirs = media_dir_repository.get_media_dir_entries(db.session)
        scope_query = _granted(peer_device_id, album_scope_query(album_id))
        # The `path` filter matches against the whole stored path here: an album has no
        # single root to make a path relative to.
        files_query = apply_list_filters(scope_query, Path("/"), filters)
        total = files_query.count()

        files = []
        for item in _page(files_query, offset, limit):
            path = Path(item.full_file_path).expanduser().resolve()
            if not path.is_file():
                continue
            location = media_dir_location(path, media_dirs)
            if location is None:
                continue  # a member outside every configured media dir has no scope
            media_dir_id, relative_path = location
            files.append(file_manifest(media_dir_id, relative_path, path, item))
        facets = list_files_facets(scope_query, filters)

        return {
            "status": "ok",
            "type": "shared_files",
            "device_id": self._service.identity.device_id,
            "media_dir_id": None,
            "relative_path": None,
            "album_id": album_id,
            "filters": filters,
            "offset": offset,
            "limit": limit,
            "total": total,
            "files": files,
            "facets": facets,
        }


def _paging(body: dict) -> tuple[int, int]:
    offset = parse_non_negative_int(body.get("offset"), "offset")
    limit = parse_non_negative_int(body.get("limit"), "limit")
    limit = min(limit, MAX_LIST_FILES_LIMIT)
    if limit == 0:
        raise ValueError("limit must be greater than zero")
    return offset, limit


def _page(files_query, offset: int, limit: int):
    return (
        files_query.order_by(MediaItem.date_taken.desc(), MediaItem.full_file_path)
        .offset(offset)
        .limit(limit)
    )


def _granted(peer_device_id: str, scope_query):
    """The scope, intersected with what this peer's grants actually cover."""
    granted_ids = granted_media_query(peer_device_id).with_entities(MediaItem.id).subquery()
    return scope_query.filter(MediaItem.id.in_(db.session.query(granted_ids.c.id)))
