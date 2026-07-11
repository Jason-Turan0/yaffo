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
from yaffo.logging_config import get_logger
from yaffo.p2p.pairing import PairingError
from yaffo.p2p.service import P2PServiceError
from yaffo.p2p.signaling import CallError


logger = get_logger(__name__, "webapp")


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

    def render_remote_panel(device: dict, remote_shared: dict | None = None):
        return render_template(
            "sharing/_remote_panel.html",
            device=device,
            media_dirs=_media_dir_choices(),
            remote_shared=remote_shared,
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

    @app.route("/sharing/devices/<device_id>/shared", methods=["POST"])
    def sharing_device_remote_shared(device_id: str):
        """Ask a peer for the files it currently grants to this device."""
        service = _service()
        if service is None:
            return _notify(gettext("Device sharing is not running."))
        context = sharing_context()
        device = next((d for d in context["devices"] if d["device_id"] == device_id), None)
        if device is None:
            return _notify(gettext("%(device)s is not a known device.", device=device_id))
        if not device["trusted"]:
            return _notify(gettext("Pair this device again before browsing shared files."))
        try:
            logger.info("browse shared files start peer=%s", device_id)
            remote_shared = service.list_shared(device_id)
        except (CallError, P2PServiceError) as exc:
            logger.warning("browse shared files failed peer=%s error=%s", device_id, exc)
            return _notify(gettext("Could not browse shared files: %(reason)s", reason=str(exc)))
        except FutureTimeoutError:
            logger.warning("browse shared files timed out peer=%s", device_id)
            return _notify(gettext("Browsing shared files timed out — is the other device online?"))
        logger.info(
            "browse shared files done peer=%s files=%d",
            device_id,
            len(remote_shared.get("files", [])) if isinstance(remote_shared, dict) else 0,
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
