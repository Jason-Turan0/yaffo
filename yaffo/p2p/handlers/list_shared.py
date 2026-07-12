from yaffo.db import db
from yaffo.db.models import GRANT_SCOPE_ALBUM
from yaffo.db.repositories import album_repository, media_dir_repository, p2p_repository
from yaffo.p2p.handlers.sharing import (
    album_scope_query,
    grant_target,
    indexed_media_query,
    peer_lookup,
)
from yaffo.p2p.messages import build_signed_message, verify_signed_message

MESSAGE_LIST_SHARED = "list_shared"


class ListSharedEndpoint:
    def __init__(self, service) -> None:
        self._service = service

    def send(self, peer_device_id: str) -> dict:
        """Ask a trusted peer for granted scopes and file counts."""
        payload = build_signed_message(self._service.identity, MESSAGE_LIST_SHARED)
        response = self._service.call(peer_device_id, payload=payload, attempt_upgrade=False)["response"]
        return self._service.expect_ok(response)

    def handle(self, body: dict) -> dict:
        """Return the scopes this peer holds grants for, with per-scope counts."""
        with self._service._session():
            denial = verify_signed_message(body, peer_lookup(), MESSAGE_LIST_SHARED)
            if denial is not None:
                return {"status": "error", "detail": denial}
            peer_device_id = body["device_id"]
            grants = p2p_repository.list_active_grants(db.session, peer_device_id)
            media_dirs = {entry.id: entry for entry in media_dir_repository.get_media_dir_entries(db.session)}

            scopes = []
            seen: set[tuple[str, str]] = set()
            for grant in grants:
                if grant.scope_type == GRANT_SCOPE_ALBUM:
                    # An album grant has no media dir and no path: it is a set of items,
                    # named by its album, and its size is its membership right now.
                    album = album_repository.get_album(db.session, grant.album_id)
                    if album is None:
                        continue  # the album was deleted; the grant authorizes nothing
                    key = ("album", str(album.id))
                    if key in seen:
                        continue
                    seen.add(key)
                    scopes.append(
                        {
                            "scope_type": GRANT_SCOPE_ALBUM,
                            "album_id": album.id,
                            "media_dir_id": None,
                            "relative_path": None,
                            "name": album.name,
                            "file_count": album_scope_query(album.id).count(),
                        }
                    )
                    continue

                media_dir = media_dirs.get(grant.media_dir_id)
                if media_dir is None:
                    continue
                root = media_dir.path.expanduser().resolve()
                target = grant_target(root, grant)
                if target is None:
                    continue
                key = (grant.media_dir_id, grant.relative_path or "")
                if key in seen:
                    continue
                seen.add(key)
                scopes.append(
                    {
                        "scope_type": grant.scope_type,
                        "media_dir_id": grant.media_dir_id,
                        "relative_path": grant.relative_path,
                        "name": media_dir.path.name or grant.media_dir_id,
                        "file_count": indexed_media_query(target).count(),
                    }
                )

        return {
            "status": "ok",
            "type": "shared_list",
            "device_id": self._service.identity.device_id,
            "scopes": scopes,
        }
