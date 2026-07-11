from pathlib import Path
from typing import Optional

from yaffo.db.models import MediaItem
from yaffo.p2p.handlers.sharing import (
    DEFAULT_LIST_FILES_LIMIT,
    MAX_LIST_FILES_LIMIT,
    apply_list_filters,
    file_manifest,
    indexed_media_query,
    list_files_facets,
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
    identity: DeviceIdentity, media_dir_id: str, relative_path: str, filters: dict, offset: int, limit: int
) -> dict:
    """One page of a scope's file manifests. `relative_path` is the scope
    being browsed ("" for a whole media dir); `filters` is a flat dict of
    optional criteria (path substring, media_type, year, month, device) the
    serving side applies *after* scoping to the grant."""
    return build_signed_message(
        identity,
        MESSAGE_LIST_FILES,
        {
            "media_dir_id": media_dir_id,
            "relative_path": relative_path,
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
        signed_fields=("media_dir_id", "relative_path", "filters", "offset", "limit"),
    )


class ListFilesEndpoint:
    def __init__(self, service) -> None:
        self._service = service

    def send(
        self,
        peer_device_id: str,
        media_dir_id: str,
        relative_path: str = "",
        filters: Optional[dict] = None,
        offset: int = 0,
        limit: int = DEFAULT_LIST_FILES_LIMIT,
    ) -> dict:
        """Fetch one page of manifests for a granted scope on a peer."""
        payload = build_list_files_request(
            self._service.identity, media_dir_id, relative_path, filters or {}, offset, limit
        )
        response = self._service.call(peer_device_id, payload=payload, attempt_upgrade=False)["response"]
        return self._service.expect_ok(response)

    def handle(self, body: dict) -> dict:
        """Return one page of granted file manifests plus scope-constrained facets."""
        with self._service._session():
            denial = verify_list_files_request(body, peer_lookup())
            if denial is not None:
                return {"status": "error", "detail": denial}
            error, media_dir_id, scope_path, root, target = resolve_scoped_request(body)
            if error is not None:
                return error
            try:
                offset = parse_non_negative_int(body.get("offset"), "offset")
                limit = parse_non_negative_int(body.get("limit"), "limit")
            except ValueError as exc:
                return {"status": "error", "detail": str(exc)}
            limit = min(limit, MAX_LIST_FILES_LIMIT)
            if limit == 0:
                return {"status": "error", "detail": "limit must be greater than zero"}
            filters = body.get("filters") or {}
            filter_error = validate_list_filters(filters)
            if filter_error is not None:
                return filter_error

            scope_query = indexed_media_query(target)
            files_query = apply_list_filters(scope_query, target, filters)
            total = files_query.count()
            files = []
            page = (
                files_query.order_by(MediaItem.date_taken.desc(), MediaItem.full_file_path)
                .offset(offset)
                .limit(limit)
            )
            for item in page:
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
            "filters": filters,
            "offset": offset,
            "limit": limit,
            "total": total,
            "files": files,
            "facets": facets,
        }
