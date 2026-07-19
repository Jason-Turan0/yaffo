"""The Sharing tab (p2p Phase 3 — see docs/development/p2p-sharing.md):
utilities-style layout with a left sidebar (sharing settings + one entry per
paired device) and per-device pages that Phase 4 extends with share grants.

HTMX conventions match the settings labels section: actions re-render their
fragment on success (plus a `sharingDevicesChanged` trigger the sidebar
listens to, so the device list stays current) and return 204 + toast on
errors (no swap, typed input survives). Everything bridges into the
P2PService in this web process (`app.extensions["p2p_service"]`); without it
— `flask run` without YAFFO_P2P_ENABLED, or a startup failure — pages render
with presence unknown and pairing/revocation disabled.
"""
from __future__ import annotations

import json
from concurrent.futures import TimeoutError as FutureTimeoutError
from datetime import datetime, timezone
from pathlib import Path

import segno
from flask import (
    Flask,
    abort,
    current_app,
    jsonify,
    make_response,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_babel import gettext, ngettext

from yaffo.db import db
from yaffo.db.models import (
    GRANT_SCOPE_ALBUM,
    GRANT_SCOPE_FOLDER,
    GRANT_SCOPE_MEDIA_DIR,
    TRUST_STATE_TRUSTED,
)
from yaffo.db.repositories import album_repository, media_dir_repository, p2p_repository
from yaffo.db.repositories.media_repository import get_distinct_months
from yaffo.demo import DEMO_ROLE_RECEIVER, demo_unsafe_allowed
from yaffo.distance_units import distance_to_kilometers
from yaffo.routes import filter_config
from yaffo.routes.filter_panel import filter_selections, gender_options, to_query_params
from yaffo.routes.selection import selection_from_args
from yaffo.logging_config import get_logger
from yaffo.p2p.pairing import PairingError
from yaffo.p2p.service import P2PServiceError
from yaffo.p2p.signaling import CallError
from yaffo.utils.settings import get_shared_download_dir, set_shared_download_dir


logger = get_logger(__name__, "webapp")

# Remote gallery paging (each render is a live signed p2p call to the peer,
# so pages stay modest).
REMOTE_FILES_PAGE_SIZE = 50
REMOTE_FILES_PAGE_SIZES = [25, 50, 100]


def _service():
    return current_app.extensions.get("p2p_service")


def _device_row(device, online, local) -> dict:
    presence = "unknown"
    if device.device_id in local:
        presence = "local"
    elif online is not None:
        presence = "online" if device.device_id in online else "offline"
    return {
        "device_id": device.device_id,
        "display_name": device.display_name or device.device_id,
        "trust_state": device.trust_state,
        "trusted": device.trust_state == TRUST_STATE_TRUSTED,
        "paired_at": device.paired_at,
        "last_seen_at": device.last_seen_at,
        "presence": presence,
    }


def sharing_context() -> dict:
    """Everything the sharing pages + sidebar need, with one presence fetch
    per render. Presence comes live from the hub (open WebSockets ARE
    presence — nothing cached or persisted): True/False per device, or
    unknown for everyone when the hub is unreachable / the engine is down.
    The device list itself is plain DB and works without the engine."""
    service = _service()
    available = service is not None and service.identity is not None
    online = None
    local = set()
    if available:
        try:
            online = service.connected_device_ids()
        except Exception:  # noqa: BLE001 — presence is display-only; never break the page
            online = None
        try:
            local = service.local_device_ids()
        except Exception:  # noqa: BLE001 — local presence is display-only too
            local = set()
    devices = [_device_row(d, online, local) for d in p2p_repository.list_known_devices(db.session)]
    context = {"available": available, "devices": devices, "outbound_shares": _outbound_share_rows()}
    if available:
        context.update(
            device_id=service.identity.device_id,
            hub_url=service.hub_url,
            hub_connected=service.hub_connected,
        )
    return context


def _notify(message: str, type: str = "error"):
    """Empty 204 that fires the global showNotification toast — no DOM swap,
    so the user's typed input is preserved on error."""
    response = make_response("", 204)
    response.headers["HX-Trigger"] = json.dumps({"showNotification": {"message": message, "type": type}})
    return response


def _with_toast(response, message: str, type: str = "success", devices_changed: bool = True):
    """Attach a toast (and, by default, the sidebar's refresh event) to a
    rendered fragment."""
    triggers = {"showNotification": {"message": message, "type": type}}
    if devices_changed:
        triggers["sharingDevicesChanged"] = True
    response = make_response(response)
    response.headers["HX-Trigger"] = json.dumps(triggers)
    return response


def _media_dir_choices() -> list[dict]:
    choices = []
    for entry in media_dir_repository.get_media_dir_entries(db.session):
        path = entry.path
        choices.append({"id": entry.id, "name": path.name or str(path), "path": str(path)})
    return choices


def _shared_download_dir_value() -> str:
    directory = get_shared_download_dir(db.session)
    return str(directory) if directory else ""


def _render_download_directory_panel():
    return render_template("sharing/_download_directory_panel.html", download_dir=_shared_download_dir_value())


def _validated_download_dir(raw_value: str) -> Path:
    if not raw_value:
        raise ValueError(gettext("Choose a download directory for shared files."))
    directory = Path(raw_value).expanduser().resolve()
    if directory.exists() and not directory.is_dir():
        raise ValueError(gettext("Choose a directory, not a file."))
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ValueError(gettext("Could not create the download directory: %(reason)s", reason=str(exc))) from exc
    return directory


def _outbound_share_rows() -> list[dict]:
    devices = {device.device_id: device for device in p2p_repository.list_known_devices(db.session)}
    media_dirs = {choice["id"]: choice for choice in _media_dir_choices()}
    albums = {album.id: album for album in album_repository.list_albums(db.session)}
    rows = []
    for grant in p2p_repository.list_active_grants(db.session):
        device = devices.get(grant.peer_device_id)
        media_dir = media_dirs.get(grant.media_dir_id or "")
        if grant.scope_type == GRANT_SCOPE_MEDIA_DIR:
            share_name = media_dir["name"] if media_dir else grant.media_dir_id or gettext("Unknown share")
        elif grant.scope_type == GRANT_SCOPE_FOLDER:
            share_name = grant.relative_path or gettext("Folder")
        elif grant.scope_type == GRANT_SCOPE_ALBUM:
            # An album grant carries no media dir and no path — it is named by its
            # album, so without this it fell through to "Unknown share".
            album = albums.get(grant.album_id)
            share_name = album.name if album else gettext("Deleted album")
        else:
            share_name = grant.relative_path or grant.media_dir_id or gettext("Unknown share")
        rows.append(
            {
                "grant_id": grant.id,
                "device_id": grant.peer_device_id,
                "device_name": (device.display_name if device else grant.peer_device_id) or grant.peer_device_id,
                "share_name": share_name,
            }
        )
    return rows


def _remote_scope_label(scope: dict) -> str:
    name = scope.get("name") or scope.get("media_dir_id") or gettext("Shared folder")
    relative_path = scope.get("relative_path") or ""
    if relative_path:
        return f"{name} / {relative_path}"
    return name


def _shared_with_me_rows(context: dict) -> tuple[list[dict], str | None]:
    service = _service()
    if service is None:
        return [], gettext("Device sharing is not running.")

    rows = []
    had_error = False
    for device in context["devices"]:
        if not device["trusted"]:
            continue
        try:
            remote_shared = service.list_shared.send(device["device_id"])
        except (CallError, P2PServiceError) as exc:
            had_error = True
            logger.warning("sidebar shared-with-me failed peer=%s error=%s", device["device_id"], exc)
            continue
        except FutureTimeoutError:
            had_error = True
            logger.warning("sidebar shared-with-me timed out peer=%s", device["device_id"])
            continue

        for scope in remote_shared.get("scopes", []) if isinstance(remote_shared, dict) else []:
            rows.append(
                {
                    "device_id": device["device_id"],
                    "device_name": device["display_name"],
                    # An album share has no media dir or path: it is browsed by album id.
                    "media_dir_id": scope.get("media_dir_id") or "",
                    "scope": scope.get("relative_path") or "",
                    "album_id": scope.get("album_id"),
                    "share_name": _remote_scope_label(scope),
                }
            )

    error = gettext("Some shared folders could not be loaded.") if had_error else None
    return rows, error


def _remote_filter_payload(selections: dict) -> dict:
    """Translate parsed querystring selections into the list_files protocol's
    filter dict, sending only what's actually set. The proximity distance is
    normalized to kilometers HERE, with this device's unit setting — the
    units on the two devices need not match."""
    payload = {}
    if selections["selected_path"]:
        payload["path"] = selections["selected_path"]
    if selections["selected_media_type"]:
        payload["media_type"] = selections["selected_media_type"]
    if selections["selected_year"]:
        payload["year"] = selections["selected_year"]
    if selections["selected_month"]:
        payload["month"] = selections["selected_month"]
    if selections["selected_device"]:
        payload["device"] = selections["selected_device"]
    if selections["selected_favorite"]:
        payload["favorite"] = True
    if selections["selected_gender"] is not None:
        payload["gender"] = selections["selected_gender"]
    if selections["selected_person_ids"]:
        payload["people"] = selections["selected_person_ids"]
        payload["person_match_type"] = selections["selected_person_match_type"]
    if selections["selected_label_ids"]:
        payload["labels"] = selections["selected_label_ids"]
        payload["labels_match_type"] = selections["selected_labels_match_type"]
    if selections["selected_tag_name"]:
        payload["tag_name"] = selections["selected_tag_name"]
        if selections["selected_tag_value"]:
            payload["tag_value"] = selections["selected_tag_value"]
    if selections["selected_location_names"]:
        payload["locations"] = selections["selected_location_names"]
        payload["location_match_type"] = selections["selected_location_match_type"]
    if selections["selected_unnamed"]:
        payload["unnamed"] = True
    if (
        selections["selected_proximity_lat"] is not None
        and selections["selected_proximity_lon"] is not None
        and selections["selected_proximity_distance"]
    ):
        payload["proximity_lat"] = selections["selected_proximity_lat"]
        payload["proximity_lon"] = selections["selected_proximity_lon"]
        payload["proximity_km"] = distance_to_kilometers(
            selections["selected_proximity_distance"], selections["selected_distance_unit"]
        )
    return payload


def _resolve_folder_grant(folder_path: str) -> tuple[str, str | None]:
    folder = Path(folder_path).expanduser().resolve()
    for entry in media_dir_repository.get_media_dir_entries(db.session):
        root = entry.path.expanduser().resolve()
        if folder == root:
            return entry.id, None
        if root in folder.parents:
            return entry.id, folder.relative_to(root).as_posix()
    raise ValueError(gettext("Choose a folder inside a configured media directory."))


def init_sharing_routes(app: Flask):
    def render_sidebar(selected_key: str = ""):
        return render_template(
            "sharing/_sidebar.html",
            sharing=sharing_context(),
            selected_key=selected_key,
        )

    def render_settings_section():
        return render_template("sharing/_devices_section.html", sharing=sharing_context())

    def render_device_panel(device_id: str):
        context = sharing_context()
        device = next((d for d in context["devices"] if d["device_id"] == device_id), None)
        if device is None:
            abort(404)
        return render_template(
            "sharing/_device_panel.html",
            sharing=context,
            device=device,
            media_dirs=_media_dir_choices(),
            albums=album_repository.list_albums(db.session),
        )

    def render_device_content(device_id: str):
        context = sharing_context()
        device = next((d for d in context["devices"] if d["device_id"] == device_id), None)
        if device is None:
            abort(404)
        return render_template(
            "sharing/_device_content.html",
            sharing=context,
            device=device,
            selected_key=device_id,
            media_dirs=_media_dir_choices(),
            albums=album_repository.list_albums(db.session),
            transfers=_transfers_for(device_id),
        )

    @app.route("/sharing", methods=["GET"])
    def sharing_index():
        return redirect(url_for("sharing_settings"))

    @app.route("/sharing/settings", methods=["GET"])
    def sharing_settings():
        return render_template(
            "sharing/settings.html",
            sharing=sharing_context(),
            selected_key="settings",
            download_dir=_shared_download_dir_value(),
        )

    @app.route("/sharing/devices/<device_id>", methods=["GET"])
    def sharing_device(device_id: str):
        context = sharing_context()
        device = next((d for d in context["devices"] if d["device_id"] == device_id), None)
        if device is None:
            abort(404)
        return render_template(
            "sharing/device.html",
            sharing=context,
            device=device,
            selected_key=device_id,
            media_dirs=_media_dir_choices(),
            albums=album_repository.list_albums(db.session),
            transfers=_transfers_for(device_id),
        )

    @app.route("/sharing/sidebar", methods=["GET"])
    def sharing_sidebar():
        """Sidebar fragment; re-fetched via the sharingDevicesChanged trigger
        after pairing/revocation/rename so the device list stays current."""
        return render_sidebar(request.args.get("selected", ""))

    @app.route("/sharing/sidebar/shared-with-me", methods=["GET"])
    def sharing_sidebar_shared_with_me():
        context = sharing_context()
        rows, error = _shared_with_me_rows(context)
        return render_template(
            "sharing/_sidebar_shared_with_me.html",
            rows=rows,
            error=error,
            selected_key=request.args.get("selected", ""),
        )

    @app.route("/sharing/grants/<int:grant_id>/revoke", methods=["POST"])
    def sharing_sidebar_grant_revoke(grant_id: int):
        """Revoke a local grant from the sidebar's Shared With Others list."""
        active_ids = {grant.id for grant in p2p_repository.list_active_grants(db.session)}
        if grant_id not in active_ids:
            return _notify(gettext("Share grant is no longer active."))
        p2p_repository.revoke_grant(db.session, grant_id)
        return _with_toast(render_sidebar(request.args.get("selected", "")), gettext("Share grant revoked."))

    @app.route("/sharing/settings/section", methods=["GET"])
    def sharing_settings_section():
        """The settings-page section fragment — the refresh button re-pulls
        this to update presence badges."""
        return render_settings_section()

    @app.route("/sharing/pairing-code", methods=["POST"])
    def sharing_pairing_code():
        """Mint a pairing code and show it as text + QR with its expiry.
        Codes live in the p2p service's memory only and burn on first use."""
        service = _service()
        if service is None:
            return _notify(gettext("Device sharing is not running."))
        code = service.peering.generate_pairing_code()
        encoded = code.encode()
        return render_template(
            "sharing/_pairing_code.html",
            code_text=encoded,
            qr_data_uri=segno.make_qr(encoded).svg_data_uri(scale=4),
            ttl_seconds=int(code.expires_at - datetime.now(tz=timezone.utc).timestamp()),
        )

    @app.route("/sharing/pair", methods=["POST"])
    def sharing_pair():
        """Accept a pairing code pasted from the other device. Blocks for
        the duration of the relay-first exchange (pairing is a one-time
        human act; HTMX shows the busy indicator meanwhile)."""
        service = _service()
        if service is None:
            return _notify(gettext("Device sharing is not running."))
        code_text = (request.form.get("code") or "").strip()
        if not code_text:
            return _notify(gettext("Paste a pairing code first."))
        try:
            result = service.peering.send_accept_pairing_code(code_text)
        except PairingError as exc:
            return _notify(gettext("Pairing failed: %(reason)s", reason=str(exc)))
        except (CallError, P2PServiceError) as exc:
            return _notify(gettext("Could not reach the other device: %(reason)s", reason=str(exc)))
        except FutureTimeoutError:
            return _notify(gettext("Pairing timed out — is the other device online?"))
        return _with_toast(
            render_settings_section(),
            gettext("Paired with %(device)s.", device=result["peer_device_id"]),
        )

    def _revoke(device_id: str):
        """Shared revoke: local trust-store flip (the enforcement) +
        best-effort signed courtesy notice; returns (ok, toast message)."""
        service = _service()
        if service is None:
            return False, gettext("Device sharing is not running.")
        try:
            outcome = service.peering.send_revoke_peer(device_id)
        except P2PServiceError as exc:
            return False, str(exc)
        except FutureTimeoutError:
            # The local revocation already committed — only the notice timed out.
            outcome = {"peer_notified": False}
        if outcome["peer_notified"]:
            return True, gettext("Device revoked. The other device was notified.")
        return True, gettext(
            "Device revoked. The other device is offline and will find out when it next tries to connect."
        )

    @app.route("/sharing/revoke", methods=["POST"])
    def sharing_revoke():
        """Revoke from the settings page's device list."""
        ok, message = _revoke((request.form.get("device_id") or "").strip())
        if not ok:
            return _notify(message)
        return _with_toast(render_settings_section(), message)

    @app.route("/sharing/devices/<device_id>/revoke", methods=["POST"])
    def sharing_device_revoke(device_id: str):
        """Revoke from the device's own page; re-renders its panel."""
        ok, message = _revoke(device_id)
        if not ok:
            return _notify(message)
        return _with_toast(render_device_content(device_id), message)

    @app.route("/sharing/devices/<device_id>/delete", methods=["POST"])
    def sharing_device_delete(device_id: str):
        """Forget a device after it has been revoked."""
        if not p2p_repository.delete_revoked_device(db.session, device_id):
            return _notify(gettext("Revoke this device before deleting it."))
        response = _notify(gettext("Device deleted."), "success")
        response.headers["HX-Redirect"] = url_for("sharing_settings")
        return response

    @app.route("/sharing/devices/<device_id>/rename", methods=["POST"])
    def sharing_device_rename(device_id: str):
        """Display names are peer-supplied at pairing but locally editable."""
        name = (request.form.get("display_name") or "").strip()
        if not name:
            return _notify(gettext("Device name cannot be empty."))
        if not p2p_repository.rename_device(db.session, device_id, name):
            return _notify(gettext("%(device)s is not a known device.", device=device_id))
        return _with_toast(render_device_content(device_id), gettext("Device renamed."))

    @app.route("/sharing/download-directory", methods=["POST"])
    def sharing_download_directory():
        directory_value = (request.form.get("download_dir") or "").strip()
        try:
            directory = _validated_download_dir(directory_value)
        except ValueError as exc:
            return _notify(str(exc))
        set_shared_download_dir(db.session, directory)
        return _with_toast(
            _render_download_directory_panel(),
            gettext("Download directory saved."),
            devices_changed=False,
        )

    @app.route("/sharing/devices/<device_id>/grants", methods=["POST"])
    def sharing_device_grant(device_id: str):
        """Create a local share grant for a trusted peer."""
        device = p2p_repository.get_known_device(db.session, device_id)
        if device is None:
            return _notify(gettext("%(device)s is not a known device.", device=device_id))
        if device.trust_state != TRUST_STATE_TRUSTED:
            return _notify(gettext("Pair this device again before sharing with it."))

        scope_type = (request.form.get("scope_type") or "").strip()
        try:
            if scope_type == GRANT_SCOPE_MEDIA_DIR:
                media_dir_id = (request.form.get("media_dir_id") or "").strip()
                if media_dir_repository.media_dir_by_id(db.session, media_dir_id) is None:
                    return _notify(gettext("Choose a configured media directory."))
                p2p_repository.create_grant(
                    db.session,
                    device_id,
                    GRANT_SCOPE_MEDIA_DIR,
                    media_dir_id=media_dir_id,
                )
            elif scope_type == GRANT_SCOPE_FOLDER:
                folder_path = (request.form.get("folder_path") or "").strip()
                if not folder_path:
                    return _notify(gettext("Choose a folder to share."))
                media_dir_id, relative_path = _resolve_folder_grant(folder_path)
                if relative_path is None:
                    p2p_repository.create_grant(
                        db.session,
                        device_id,
                        GRANT_SCOPE_MEDIA_DIR,
                        media_dir_id=media_dir_id,
                    )
                else:
                    p2p_repository.create_grant(
                        db.session,
                        device_id,
                        GRANT_SCOPE_FOLDER,
                        media_dir_id=media_dir_id,
                        relative_path=relative_path,
                    )
            elif scope_type == GRANT_SCOPE_ALBUM:
                album_id = request.form.get("album_id", type=int)
                if album_id is None or album_repository.get_album(db.session, album_id) is None:
                    return _notify(gettext("Choose an album to share."))
                p2p_repository.create_grant(
                    db.session, device_id, GRANT_SCOPE_ALBUM, album_id=album_id
                )
            else:
                return _notify(gettext("Choose what to share."))
        except ValueError as exc:
            return _notify(str(exc))
        return _with_toast(render_device_panel(device_id), gettext("Share grant added."))

    @app.route("/sharing/devices/<device_id>/files", methods=["GET"])
    def sharing_device_files(device_id: str):
        """Remote gallery for one shared scope — behaves like the home page:
        filter panel on the left, photo grid on the right, filters and
        pagination as plain GET parameters. Each render is one live
        list_files call to the peer, which scopes to the grant FIRST and
        then applies these filters; the response's facets populate the
        year/device selects. Previews load lazily via sharing_device_preview.
        """
        context = sharing_context()
        device = next((d for d in context["devices"] if d["device_id"] == device_id), None)
        if device is None:
            abort(404)
        media_dir_id = (request.args.get("media_dir_id") or "").strip()
        album_id = request.args.get("album_id", type=int)
        # A scope is a path (media dir + optional subfolder) or an album — an album's
        # members are a set of items that can live in several media dirs, so it has
        # neither of those.
        if not media_dir_id and album_id is None:
            return redirect(url_for("sharing_device", device_id=device_id))
        scope = (request.args.get("scope") or "").strip()
        label = (request.args.get("label") or "").strip()

        page = max(request.args.get("page", default=1, type=int) or 1, 1)
        page_size = request.args.get("page-size", type=int) or REMOTE_FILES_PAGE_SIZE
        page_size = min(max(page_size, 1), max(REMOTE_FILES_PAGE_SIZES))

        selections = filter_selections(db.session, request.args)
        filter_payload = _remote_filter_payload(selections)

        error = None
        result = {}
        service = _service()
        if service is None:
            error = gettext("Device sharing is not running.")
        elif not device["trusted"]:
            error = gettext("This device is revoked. Pair it again before browsing shared files.")
        else:
            try:
                result = service.list_files.send(
                    device_id,
                    media_dir_id,
                    scope,
                    filter_payload,
                    offset=(page - 1) * page_size,
                    limit=page_size,
                    album_id=album_id,
                )
            except (CallError, P2PServiceError) as exc:
                logger.warning("browse shared files failed peer=%s error=%s", device_id, exc)
                error = gettext("Could not load shared files: %(reason)s", reason=str(exc))
            except FutureTimeoutError:
                error = gettext("Loading shared files timed out — is the other device online?")

        facets = result.get("facets") or {}
        filters = {
            # The option lists the reused filter partials render from — all
            # facets come from the PEER's library (scoped to the grant), not
            # the local DB. People/label entries are the peer's own entity
            # ids, echoed back to it when selected.
            "years": facets.get("years", []),
            "months": get_distinct_months(),
            "devices": facets.get("devices", []),
            "people": facets.get("people", []),
            "labels": facets.get("labels", []),
            "tag_names": facets.get("tag_names", []),
            "location_names": facets.get("locations", []),
            "genders": gender_options(),
            **selections,
        }
        total = result.get("total", 0)
        # A remote file is identified by the PEER's media item id — the handle its
        # manifests hand out, and what a pull is authorized against.
        selection = selection_from_args(request.args, total=total, cast=int)
        # Pagination links must carry the scope, the filters AND the selection, or
        # paging would silently drop what the user has ticked.
        page_params = {
            "media_dir_id": media_dir_id,
            "scope": scope,
            "label": label,
            "album_id": album_id,
            **to_query_params(selections),
            **selection.query_params,
        }
        return render_template(
            "sharing/files.html",
            device=device,
            media_dir_id=media_dir_id,
            scope=scope,
            label=label,
            filters=filters,
            files=result.get("files", []),
            error=error,
            download_dir=_shared_download_dir_value(),
            transfers=_transfers_for(device_id),
            album_id=album_id,
            selection=selection,
            page_params=page_params,
            filter_layout=filter_config.load_layout(db.session),
            filter_default_keys=filter_config.default_keys(),
            pagination={
                "current_page": page,
                "total_items": total,
                "page_size": page_size,
                "page_sizes": REMOTE_FILES_PAGE_SIZES,
            },
        )

    @app.route("/sharing/devices/<device_id>/tag-values", methods=["GET"])
    def sharing_device_tag_values(device_id: str):
        """Peer-side tag values for the remote gallery's tag filter — same
        response shape as the home page's /api/tag-values, but the values
        come from the peer, scoped to the grant (a facets-only list_files
        call; no manifests wanted, hence limit=1)."""
        service = _service()
        tag_name = (request.args.get("tag_name") or "").strip()
        media_dir_id = (request.args.get("media_dir_id") or "").strip()
        scope = (request.args.get("scope") or "").strip()
        if not tag_name or not media_dir_id:
            return jsonify({"error": gettext("The tag_name parameter is required"), "code": "tag_name_required"}), 400
        if service is None:
            return jsonify({"tag_name": tag_name, "values": []}), 503
        try:
            result = service.list_files.send(device_id, media_dir_id, scope, {"tag_name": tag_name}, limit=1)
        except (CallError, P2PServiceError):
            return jsonify({"tag_name": tag_name, "values": []}), 502
        except FutureTimeoutError:
            return jsonify({"tag_name": tag_name, "values": []}), 504
        return jsonify({"tag_name": tag_name, "values": (result.get("facets") or {}).get("tag_values", [])})

    @app.route("/sharing/devices/<device_id>/preview", methods=["GET"])
    def sharing_device_preview(device_id: str):
        """Proxy one gallery preview from the peer (which downscales and
        recompresses before sending). Long-lived private browser caching —
        the grid busts the cache via the file's mtime in the URL. Failures
        are plain error statuses; the <img> data-fallback swaps in a
        placeholder."""
        service = _service()
        if service is None:
            abort(503)
        media_item_id = request.args.get("media_item_id", type=int)
        if media_item_id is None:
            abort(404)
        try:
            data = service.pull_preview.send(device_id, media_item_id)
        except (CallError, P2PServiceError):
            abort(502)
        except FutureTimeoutError:
            abort(504)
        response = make_response(data)
        response.headers["Content-Type"] = "image/jpeg"
        response.headers["Cache-Control"] = "private, max-age=604800"
        return response

    def _transfer_prerequisites(device_id: str):
        """Shared validation for the transfer-enqueue routes. Returns
        (service, device, destination_root, error_response) — exactly one of
        the first three tuple or error_response is None-ness to check."""
        service = _service()
        if service is None:
            return None, None, None, _notify(gettext("Device sharing is not running."))
        device = p2p_repository.get_known_device(db.session, device_id)
        if device is None:
            return None, None, None, _notify(gettext("%(device)s is not a known device.", device=device_id))
        if device.trust_state != TRUST_STATE_TRUSTED:
            return None, None, None, _notify(gettext("Pair this device again before pulling files from it."))
        destination_root = get_shared_download_dir(db.session)
        if destination_root is None:
            return None, None, None, _notify(gettext("Choose a download directory for shared files first."))
        try:
            destination_root = _validated_download_dir(str(destination_root))
        except ValueError as exc:
            return None, None, None, _notify(str(exc))
        return service, device, destination_root, None

    def _transfer_queued_toast(message: str):
        """204 + toast + the trigger the transfers panel listens for while
        idle, so an enqueued batch makes the panel appear immediately."""
        response = make_response("", 204)
        response.headers["HX-Trigger"] = json.dumps(
            {
                "showNotification": {"message": message, "type": "success"},
                "sharingTransfersChanged": True,
            }
        )
        return response

    def _transfers_for(device_id: str) -> list[dict]:
        service = _service()
        if service is None:
            return []
        try:
            return service.transfers.snapshot(device_id)
        except Exception:  # noqa: BLE001 — status is display-only; never break the page
            logger.exception("transfer snapshot failed peer=%s", device_id)
            return []

    def _render_transfers_panel(device_id: str):
        return render_template(
            "sharing/_transfers_panel.html",
            device={"device_id": device_id},
            transfers=_transfers_for(device_id),
        )

    @app.route("/sharing/devices/<device_id>/transfers", methods=["GET"])
    def sharing_device_transfers(device_id: str):
        """The self-polling transfers status fragment."""
        return _render_transfers_panel(device_id)

    @app.route("/sharing/devices/<device_id>/transfers/pull", methods=["POST"])
    @demo_unsafe_allowed(DEMO_ROLE_RECEIVER)
    def sharing_device_pull_selected(device_id: str):
        """Pull the remote gallery's selection as one background batch.

        The selection rides the querystring (routes/selection.py), exactly as the
        gallery rendered it: `select=all` (everything matching the scope + filters —
        including files on pages never rendered — minus any `exclude_id`), or a list
        of `select_id` media item ids.

        Either way the batch resolves the selection against the manifest it snapshots
        FROM THE PEER: the browser sends ids, never sizes or mtimes. Those seed the
        resume sidecars, and a resumed .partial is only continued when the source still
        matches them, so they have to come from the source of truth."""
        service, device, destination_root, error = _transfer_prerequisites(device_id)
        if error is not None:
            return error

        media_dir_id = (request.args.get("media_dir_id") or "").strip()
        album_id = request.args.get("album_id", type=int)
        if not media_dir_id and album_id is None:
            return _notify(gettext("Could not start the download: the shared scope is missing."))
        scope = (request.args.get("scope") or "").strip()
        label = (request.args.get("label") or "").strip()
        selections = filter_selections(db.session, request.args)
        filter_payload = _remote_filter_payload(selections)

        selection = selection_from_args(request.args, total=0, cast=int)
        if selection.all:
            include_ids, exclude_ids = None, sorted(selection.excluded)
        elif selection.ids:
            include_ids, exclude_ids = sorted(selection.ids), None
        else:
            return _notify(gettext("Select the files to pull first."))

        try:
            service.transfers.start_batch(
                device_id,
                device.display_name or device.device_id,
                media_dir_id,
                scope,
                label or scope or media_dir_id,
                filter_payload,
                destination_root,
                # The destination folder: the browsed scope for a path share (unchanged),
                # and — since an album has no scope path — the album's name for an album
                # share.
                scope or label or media_dir_id,
                include_ids=include_ids,
                exclude_ids=exclude_ids,
                album_id=album_id,
            )
        except (CallError, P2PServiceError, ValueError) as exc:
            return _notify(gettext("Could not start the download: %(reason)s", reason=str(exc)))
        except FutureTimeoutError:
            return _notify(gettext("Queuing the transfer timed out — is device sharing running?"))

        if selection.all:
            message = gettext("Download queued — everything selected in this view will be pulled.")
        else:
            message = ngettext(
                "Pull of %(count)s file queued.",
                "Pull of %(count)s files queued.",
                len(selection.ids),
                count=len(selection.ids),
            )
        return _transfer_queued_toast(message)

    @app.route("/sharing/devices/<device_id>/transfers/<batch_id>/cancel", methods=["POST"])
    def sharing_transfer_cancel(device_id: str, batch_id: str):
        service = _service()
        if service is None:
            return _notify(gettext("Device sharing is not running."))
        try:
            cancelled = service.transfers.cancel(batch_id)
        except (P2PServiceError, FutureTimeoutError):
            cancelled = False
        message = gettext("Transfer cancelled.") if cancelled else gettext("Transfer is no longer active.")
        return _with_toast(_render_transfers_panel(device_id), message, devices_changed=False)

    @app.route("/sharing/devices/<device_id>/transfers/<batch_id>/continue", methods=["POST"])
    def sharing_transfer_continue(device_id: str, batch_id: str):
        """Continue-anyway for a batch paused at the soft relay budget."""
        service = _service()
        if service is None:
            return _notify(gettext("Device sharing is not running."))
        try:
            resumed = service.transfers.allow_relay_overage(batch_id)
        except (P2PServiceError, FutureTimeoutError):
            resumed = False
        message = (
            gettext("Continuing over the relay.") if resumed else gettext("Transfer is no longer active.")
        )
        return _with_toast(_render_transfers_panel(device_id), message, devices_changed=False)

    @app.route("/sharing/devices/<device_id>/transfers/<batch_id>/delete", methods=["POST"])
    def sharing_transfer_delete(device_id: str, batch_id: str):
        service = _service()
        if service is None:
            return _notify(gettext("Device sharing is not running."))
        try:
            deleted = service.transfers.delete(batch_id)
        except (P2PServiceError, FutureTimeoutError):
            deleted = False
        # Dismissing a finished transfer needs no toast — the card disappearing
        # is the feedback. Still surface it if it unexpectedly couldn't be
        # dismissed (e.g. it became active again between render and click).
        if not deleted:
            return _notify(gettext("Cancel or finish this transfer before deleting it."))
        return _render_transfers_panel(device_id)
