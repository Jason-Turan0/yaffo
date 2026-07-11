from yaffo.db import db
from yaffo.db.repositories import media_dir_repository, p2p_repository
from yaffo.p2p.errors import P2PServiceError
from yaffo.p2p.handlers.sharing import grant_target, indexed_media_query, peer_lookup
from yaffo.p2p.messages import build_list_shared_request, verify_list_shared_request


class ListSharedEndpoint:
    def __init__(self, service) -> None:
        self._service = service

    def send(self, peer_device_id: str) -> dict:
        """Ask a trusted peer for granted scopes and file counts."""
        payload = build_list_shared_request(self._service.identity)
        return self._expect_ok(self._service.call(peer_device_id, payload=payload, attempt_upgrade=False)["response"])

    def handle(self, body: dict) -> dict:
        """Return the scopes this peer holds grants for, with per-scope counts."""
        with self._service._session():
            denial = verify_list_shared_request(body, peer_lookup())
            if denial is not None:
                return {"status": "error", "detail": denial}
            peer_device_id = body["device_id"]
            grants = p2p_repository.list_active_grants(db.session, peer_device_id)
            media_dirs = {entry.id: entry for entry in media_dir_repository.get_media_dir_entries(db.session)}

            scopes = []
            seen: set[tuple[str, str]] = set()
            for grant in grants:
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

    def _expect_ok(self, response: dict) -> dict:
        if not isinstance(response, dict) or response.get("status") != "ok":
            detail = response.get("detail", "peer reported an error") if isinstance(response, dict) else "no response"
            raise P2PServiceError(detail)
        return response
