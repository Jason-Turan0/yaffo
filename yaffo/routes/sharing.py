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
from flask_babel import gettext

from yaffo.db import db
from yaffo.db.models import (
    GRANT_SCOPE_FOLDER,
    GRANT_SCOPE_MEDIA_DIR,
    TRUST_STATE_TRUSTED,
)
from yaffo.db.repositories import media_dir_repository, p2p_repository
from yaffo.db.repositories.media_repository import get_distinct_months
from yaffo.distance_units import distance_to_kilometers
from yaffo.routes.filter_panel import filter_selections, gender_options
from yaffo.logging_config import get_logger
from yaffo.p2p.pairing import PairingError
from yaffo.p2p.service import P2PServiceError
from yaffo.p2p.signaling import CallError


logger = get_logger(__name__, "webapp")

# Remote gallery paging (each render is a live signed p2p call to the peer,
# so pages stay modest).
REMOTE_FILES_PAGE_SIZE = 50
REMOTE_FILES_PAGE_SIZES = [25, 50, 100]


def _service():
    return current_app.extensions.get("p2p_service")


def _device_row(device, online) -> dict:
    return {
        "device_id": device.device_id,
        "display_name": device.display_name or device.device_id,
        "trust_state": device.trust_state,
        "trusted": device.trust_state == TRUST_STATE_TRUSTED,
        "paired_at": device.paired_at,
        "last_seen_at": device.last_seen_at,
        "presence": ("online" if device.device_id in online else "offline") if online is not None else "unknown",
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
    if available:
        try:
            online = service.connected_device_ids()
        except Exception:  # noqa: BLE001 — presence is display-only; never break the page
            online = None
    devices = [_device_row(d, online) for d in p2p_repository.list_known_devices(db.session)]
    context = {"available": available, "devices": devices}
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


def _active_grant_rows(peer_device_id: str) -> list[dict]:
    media_dirs = {choice["id"]: choice for choice in _media_dir_choices()}
    rows = []
    for grant in p2p_repository.list_active_grants(db.session, peer_device_id):
        media_dir = media_dirs.get(grant.media_dir_id or "")
        if grant.scope_type == GRANT_SCOPE_MEDIA_DIR:
            scope_label = gettext("Media directory")
            name = media_dir["name"] if media_dir else grant.media_dir_id
            detail = media_dir["path"] if media_dir else gettext("Media directory was removed.")
        elif grant.scope_type == GRANT_SCOPE_FOLDER:
            scope_label = gettext("Folder")
            name = grant.relative_path
            detail = (
                str(Path(media_dir["path"]) / grant.relative_path)
                if media_dir and grant.relative_path
                else gettext("Media directory was removed.")
            )
        else:
            scope_label = grant.scope_type
            name = grant.relative_path or grant.media_dir_id or gettext("Unknown scope")
            detail = ""
        rows.append(
            {
                "id": grant.id,
                "scope_label": scope_label,
                "name": name,
                "detail": detail,
            }
        )
    return rows


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
            grants=_active_grant_rows(device_id),
            remote_shared=None,
        )

    def render_remote_panel(device: dict, remote_shared: dict | None = None, remote_error: str | None = None):
        return render_template(
            "sharing/_remote_panel.html",
            device=device,
            media_dirs=_media_dir_choices(),
            remote_shared=remote_shared,
            remote_error=remote_error,
        )

    @app.route("/sharing", methods=["GET"])
    def sharing_index():
        return redirect(url_for("sharing_settings"))

    @app.route("/sharing/settings", methods=["GET"])
    def sharing_settings():
        return render_template("sharing/settings.html", sharing=sharing_context(), selected_key="settings")

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
            grants=_active_grant_rows(device_id),
            remote_shared=None,
        )

    @app.route("/sharing/sidebar", methods=["GET"])
    def sharing_sidebar():
        """Sidebar fragment; re-fetched via the sharingDevicesChanged trigger
        after pairing/revocation/rename so the device list stays current."""
        return render_template(
            "sharing/_sidebar.html",
            sharing=sharing_context(),
            selected_key=request.args.get("selected", ""),
        )

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
        code = service.generate_pairing_code()
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
            result = service.accept_pairing_code(code_text)
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
            outcome = service.revoke_peer(device_id)
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
        return _with_toast(render_device_panel(device_id), message)

    @app.route("/sharing/devices/<device_id>/rename", methods=["POST"])
    def sharing_device_rename(device_id: str):
        """Display names are peer-supplied at pairing but locally editable."""
        name = (request.form.get("display_name") or "").strip()
        if not name:
            return _notify(gettext("Device name cannot be empty."))
        if not p2p_repository.rename_device(db.session, device_id, name):
            return _notify(gettext("%(device)s is not a known device.", device=device_id))
        return _with_toast(render_device_panel(device_id), gettext("Device renamed."))

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
            else:
                return _notify(gettext("Choose what to share."))
        except ValueError as exc:
            return _notify(str(exc))
        return _with_toast(render_device_panel(device_id), gettext("Share grant added."), devices_changed=False)

    @app.route("/sharing/devices/<device_id>/grants/<int:grant_id>/revoke", methods=["POST"])
    def sharing_device_grant_revoke(device_id: str, grant_id: int):
        """Revoke one local share grant; the row stays as history."""
        active_ids = {grant.id for grant in p2p_repository.list_active_grants(db.session, device_id)}
        if grant_id not in active_ids:
            return _notify(gettext("Share grant is no longer active."))
        p2p_repository.revoke_grant(db.session, grant_id)
        return _with_toast(render_device_panel(device_id), gettext("Share grant revoked."), devices_changed=False)

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
        if not media_dir_id:
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
                result = service.list_shared_files(
                    device_id,
                    media_dir_id,
                    scope,
                    filter_payload,
                    offset=(page - 1) * page_size,
                    limit=page_size,
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
        return render_template(
            "sharing/files.html",
            device=device,
            media_dir_id=media_dir_id,
            scope=scope,
            label=label,
            filters=filters,
            files=result.get("files", []),
            error=error,
            media_dirs=_media_dir_choices(),
            pagination={
                "current_page": page,
                "total_items": result.get("total", 0),
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
            result = service.list_shared_files(device_id, media_dir_id, scope, {"tag_name": tag_name}, limit=1)
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
        media_dir_id = (request.args.get("media_dir_id") or "").strip()
        relative_path = (request.args.get("path") or "").strip()
        if not media_dir_id or not relative_path:
            abort(404)
        try:
            data = service.pull_preview(device_id, media_dir_id, relative_path)
        except (CallError, P2PServiceError):
            abort(502)
        except FutureTimeoutError:
            abort(504)
        response = make_response(data)
        response.headers["Content-Type"] = "image/jpeg"
        response.headers["Cache-Control"] = "private, max-age=604800"
        return response

    @app.route("/sharing/devices/<device_id>/shared", methods=["POST"])
    def sharing_device_remote_shared(device_id: str):
        """Ask a peer for the scopes it currently grants to this device.
        Fired by the panel's own load trigger (and its Try-again button), so
        errors render inline — a 204 toast would strand the loading
        placeholder."""
        context = sharing_context()
        device = next((d for d in context["devices"] if d["device_id"] == device_id), None)
        if device is None:
            return _notify(gettext("%(device)s is not a known device.", device=device_id))
        if not device["trusted"]:
            return render_remote_panel(device)  # the template shows the revoked state
        service = _service()
        if service is None:
            return render_remote_panel(device, remote_error=gettext("Device sharing is not running."))
        try:
            logger.info("browse shared scopes start peer=%s", device_id)
            remote_shared = service.list_shared(device_id)
        except (CallError, P2PServiceError) as exc:
            logger.warning("browse shared scopes failed peer=%s error=%s", device_id, exc)
            return render_remote_panel(
                device, remote_error=gettext("Could not load shared folders: %(reason)s", reason=str(exc))
            )
        except FutureTimeoutError:
            logger.warning("browse shared scopes timed out peer=%s", device_id)
            return render_remote_panel(
                device, remote_error=gettext("Loading shared folders timed out — is the other device online?")
            )
        logger.info(
            "browse shared scopes done peer=%s scopes=%d",
            device_id,
            len(remote_shared.get("scopes", [])) if isinstance(remote_shared, dict) else 0,
        )
        return render_remote_panel(device, remote_shared)

    @app.route("/sharing/devices/<device_id>/pull", methods=["POST"])
    def sharing_device_pull_file(device_id: str):
        """Pull one remote file into a selected local media directory."""
        service = _service()
        if service is None:
            return _notify(gettext("Device sharing is not running."))
        device = p2p_repository.get_known_device(db.session, device_id)
        if device is None:
            return _notify(gettext("%(device)s is not a known device.", device=device_id))
        if device.trust_state != TRUST_STATE_TRUSTED:
            return _notify(gettext("Pair this device again before pulling files from it."))

        destination_media_dir_id = (request.form.get("destination_media_dir_id") or "").strip()
        destination = media_dir_repository.media_dir_by_id(db.session, destination_media_dir_id)
        if destination is None:
            return _notify(gettext("Choose a local media directory."))

        remote_media_dir_id = (request.form.get("remote_media_dir_id") or "").strip()
        relative_path = (request.form.get("relative_path") or "").strip()
        expected_sha256 = (request.form.get("sha256") or "").strip() or None
        try:
            result = service.pull_file(
                device_id,
                remote_media_dir_id,
                relative_path,
                destination.path,
                expected_sha256=expected_sha256,
            )
        except (CallError, P2PServiceError, ValueError) as exc:
            return _notify(gettext("Could not pull file: %(reason)s", reason=str(exc)))
        except FutureTimeoutError:
            return _notify(gettext("Pulling the file timed out — is the other device online?"))
        return _notify(gettext("Pulled %(file)s.", file=result["relative_path"]), type="success")
