"""A sync's Account step and the option pickers that read the account.

- Sign in: the browser goes to the provider's page and comes back to Yaffo's
  loopback root (`/`, no path; see yaffo/sync/oauth.py), which `sync_oauth_return`
  answers before the normal routes when the `state` names a sign-in Yaffo started.
  When the browser can't reach Yaffo that way, the device-code fallback shows a code
  to enter on the provider's site while the page polls.
- Account choice: connections are shared, so an existing account for the provider
  is offered (and preselected for a new sync).
- Pickers: `remote_folder` / `remote_collection` options list what's in the account
  by calling the adapter's `choices` hook live, with read grants only.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from flask import Flask, abort, jsonify, redirect, request, url_for
from flask_babel import gettext
from sqlalchemy.orm import Session

from yaffo.db import db
from yaffo.db.models import Automation
from yaffo.db.repositories import automation_repository as repo
from yaffo.db.repositories import sync_repository
from yaffo.logging_config import get_logger
from yaffo.sync import access, connections, oauth, setup
from yaffo.sync.adapters import LoadedAdapter
from yaffo.sync.handlers import HookContext
from yaffo.sync.schemas import PASS_INCREMENTAL, SyncConfig
from yaffo.sync.starlark_adapter import HookFailed, StarlarkSourceHandler, StarlarkTargetHandler

logger = get_logger(__name__, 'webapp')

MAX_CHOICES = 500


@dataclass(frozen=True)
class AccountChoice:
    key: str
    label: str


@dataclass
class AccountView:
    """The Account step: which account the sync uses, and what's wrong with it."""
    provider_label: str
    connection_key: str | None
    label: str | None
    problem: str | None
    shared_with: int
    others: list[AccountChoice] = field(default_factory=list)
    device_code: bool = False

    @property
    def done(self) -> bool:
        return self.connection_key is not None and self.problem is None

    @property
    def problem_text(self) -> str | None:
        return account_problem_text(self.problem, self.provider_label, self.label)


def account_problem_text(problem: str | None, provider_label: str, label: str | None) -> str | None:
    if problem == access.ACCOUNT_NEEDS_SIGN_IN:
        return gettext("%(service)s needs you to sign in to %(account)s again.", service=provider_label,
                       account=label or provider_label)
    if problem == access.ACCOUNT_NEEDS_SCOPES:
        return gettext("This sync needs more access to %(account)s than it has. Sign in again to allow it.",
                       account=label or provider_label)
    return None


def account_view(session: Session, automation: Automation, adapter: LoadedAdapter,
                 config: SyncConfig) -> AccountView | None:
    if not adapter.needs_connection or adapter.preset is None:
        return None
    connection = connections.get(session, config.connection_key)
    problem = access.account_problem(session, adapter, config) if config.connection_key else None
    shared = 0
    if connection is not None and config.connection_key:
        shared = len([i for i in connections.references(session, config.connection_key) if i != automation.id])
    others = [AccountChoice(key, c.account_label or c.account_id)
              for key, c in connections.find_for_provider(session, adapter.provider) if key != config.connection_key]
    return AccountView(provider_label=adapter.preset.label,
                       connection_key=config.connection_key if connection is not None else None,
                       label=(connection.account_label or connection.account_id) if connection else None,
                       problem=problem, shared_with=shared, others=others,
                       device_code=adapter.preset.device_code_url is not None)


def _require_sync(slug: str) -> Automation:
    automation = repo.get_by_slug(db.session, slug)
    if automation is None or not automation.is_sync:
        abort(404)
    return automation


def _sign_in_scopes(session: Session, adapter: LoadedAdapter, config: SyncConfig, fresh: bool) -> list[str]:
    assert adapter.preset is not None
    existing = None
    if not fresh:
        connection = connections.get(session, config.connection_key)
        existing = connection.scopes if connection is not None else None
    return oauth.requested_scopes(adapter.preset, adapter.scopes, existing)


def _server_port() -> int:
    try:
        return int(request.environ.get("SERVER_PORT") or request.host.rsplit(":", 1)[-1])
    except ValueError:
        return 80


def _back(automation: Automation, **params: str):
    return redirect(url_for("automations_show", slug=automation.slug, **params))


def _hook_ctx(config: SyncConfig, automation: Automation) -> HookContext:
    runtime = sync_repository.load_sync(automation).runtime
    return HookContext(options=config.options, state=runtime.state, run_id=None, pass_kind=PASS_INCREMENTAL,
                       preview=True, automation_id=automation.id)


def _choice_items(raw: Any) -> list[dict]:
    items = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not isinstance(item.get("label"), str):
            continue
        items.append({"id": item["id"], "label": item["label"][:200],
                      "has_children": bool(item.get("has_children")), "suggested": bool(item.get("suggested"))})
    return items[:MAX_CHOICES]


def init_sync_account_routes(app: Flask) -> None:
    @app.before_request
    def sync_oauth_return():
        """The loopback return address of a browser sign-in is Yaffo's bare root."""
        if request.path != "/" or request.method != "GET":
            return None
        state = request.args.get("state")
        pending = oauth.pending_sign_in(db.session, state) if state else None
        if pending is None:
            return None
        return_to = pending.return_to
        try:
            signed_in = oauth.finish_sign_in(db.session, state, request.args.to_dict(), http=access.make_http())
        except oauth.SignInError as e:
            logger.warning(f"sign-in failed: {e.code} {e}")
            joiner = "&" if "?" in return_to else "?"
            return redirect(f"{return_to}{joiner}sync_error=sign_in_failed")
        logger.info(f"browser sign-in completed: {signed_in.connection_key}")
        if signed_in.automation_slug:
            automation = repo.get_by_slug(db.session, signed_in.automation_slug)
            if automation is not None and automation.is_sync:
                try:
                    setup.set_connection(db.session, automation, signed_in.connection_key)
                except ValueError:
                    pass
        return redirect(signed_in.return_to)

    @app.route("/utilities/automations/<slug>/sync/sign-in", methods=["POST"])
    def sync_sign_in(slug: str):
        automation = _require_sync(slug)
        adapter = setup.adapter_of(db.session, automation)
        config = sync_repository.load_sync(automation).config
        if adapter.preset is None:
            abort(400)
        fresh = request.form.get("fresh") == "1"
        scopes = _sign_in_scopes(db.session, adapter, config, fresh)
        return_to = request.host_url.rstrip("/") + url_for("automations_show", slug=automation.slug)
        url = oauth.begin_sign_in(db.session, adapter.preset, scopes, _server_port(), return_to, automation.slug)
        return redirect(url)

    @app.route("/utilities/automations/<slug>/sync/device-code", methods=["POST"])
    def sync_device_code(slug: str):
        automation = _require_sync(slug)
        adapter = setup.adapter_of(db.session, automation)
        config = sync_repository.load_sync(automation).config
        if adapter.preset is None:
            abort(400)
        scopes = _sign_in_scopes(db.session, adapter, config, request.form.get("fresh") == "1")
        try:
            handle, pending = oauth.begin_device_code(db.session, adapter.preset, scopes, automation.slug,
                                                      http=access.make_http())
        except oauth.SignInError as e:
            logger.warning(f"device-code sign-in failed to start: {e.code} {e}")
            return jsonify({"error": gettext("%(service)s didn't start the sign-in. Try again.",
                                             service=adapter.preset.label)}), 502
        return jsonify({"handle": handle, "user_code": pending.user_code,
                        "verification_uri": pending.verification_uri, "interval": pending.interval})

    @app.route("/utilities/automations/<slug>/sync/device-code/poll", methods=["POST"])
    def sync_device_code_poll(slug: str):
        automation = _require_sync(slug)
        handle = request.form.get("handle") or (request.get_json(silent=True) or {}).get("handle") or ""
        try:
            status, signed_in = oauth.poll_device_code(db.session, handle, http=access.make_http())
        except oauth.SignInError as e:
            logger.warning(f"device-code sign-in for '{automation.slug}' failed: {e.code} {e}")
            text = (gettext("The code expired. Start again.") if e.code == "expired_token"
                    else gettext("The sign-in was refused."))
            return jsonify({"status": "failed", "error": text})
        if status == oauth.DEVICE_DONE and signed_in is not None:
            logger.info(f"device-code sign-in completed: {signed_in.connection_key} for '{automation.slug}'")
            setup.set_connection(db.session, automation, signed_in.connection_key)
            return jsonify({"status": "done"})
        return jsonify({"status": "pending"})

    @app.route("/utilities/automations/<slug>/sync/account", methods=["POST"])
    def sync_use_account(slug: str):
        automation = _require_sync(slug)
        key = request.form.get("connection_key") or ""
        try:
            setup.set_connection(db.session, automation, key)
        except ValueError:
            return _back(automation, sync_error="account_unknown")
        return _back(automation)

    @app.route("/utilities/automations/<slug>/sync/choices", methods=["GET"])
    def sync_choices(slug: str):
        """One level of an option picker: the folders (or albums) under `parent`."""
        automation = _require_sync(slug)
        adapter = setup.adapter_of(db.session, automation)
        config = sync_repository.load_sync(automation).config
        option = request.args.get("option") or ""
        spec = adapter.options.get(option)
        if spec is None or not spec["type"].startswith("remote_") or not adapter.has_choices:
            abort(404)
        if access.account_problem(db.session, adapter, config):
            return jsonify({"error": gettext("Sign in first.")}), 409
        client = access.attach(db.session, adapter, config, read_only=True)
        handler = adapter.handler
        assert isinstance(handler, (StarlarkSourceHandler, StarlarkTargetHandler)) and client is not None
        try:
            envelope = handler.choices(option, request.args.get("parent") or None, request.args.get("cursor") or None,
                                       _hook_ctx(config, automation))
        except HookFailed as e:
            logger.warning(f"choices for '{automation.slug}' failed: {e}")
            return jsonify({"error": gettext("Couldn't list what's in %(service)s.",
                                             service=adapter.preset.label if adapter.preset else "")}), 502
        if client.auth_failed or (envelope.get("error") or {}).get("code") == "auth":
            return jsonify({"error": gettext("Sign in again first.")}), 409
        if envelope.get("error"):
            code = envelope["error"].get("code")
            text = (gettext("That folder no longer exists.") if code == "not_found"
                    else gettext("%(service)s didn't answer. Try again.",
                                 service=adapter.preset.label if adapter.preset else ""))
            return jsonify({"error": text}), 502
        next_cursor = envelope.get("next_cursor")
        return jsonify({"items": _choice_items(envelope.get("items")),
                        "next_cursor": next_cursor if isinstance(next_cursor, str) else None})
