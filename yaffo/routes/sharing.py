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
from yaffo.db.models import TRUST_STATE_TRUSTED
from yaffo.db.repositories import p2p_repository
from yaffo.p2p.pairing import PairingError
from yaffo.p2p.service import P2PServiceError
from yaffo.p2p.signaling import CallError


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


def init_sharing_routes(app: Flask):
    def render_settings_section():
        return render_template("sharing/_devices_section.html", sharing=sharing_context())

    def render_device_panel(device_id: str):
        context = sharing_context()
        device = next((d for d in context["devices"] if d["device_id"] == device_id), None)
        if device is None:
            abort(404)
        return render_template("sharing/_device_panel.html", sharing=context, device=device)

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
        return render_template("sharing/device.html", sharing=context, device=device, selected_key=device_id)

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
