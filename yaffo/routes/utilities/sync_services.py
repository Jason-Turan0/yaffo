"""The service builder: *Add a new service…* and the page where a custom adapter is
built through a chat, tested, and published.

The page shows the chat beside five tabs: Overview (what the draft does), Test account
(the user's own OAuth app if the service needs one, and the account Test reads from),
Web access (the endpoints the static check found: exactly what Publish grants), Code,
and Test. The user's Test reads live with the test account (their click is the
approval); writes are never sent. Its result also goes to the model with the next
request. Publishing approves the endpoint list; republishing a draft that calls more
re-asks every sync using the service (the grant set is part of their consent).

Routes are under /utilities/automations/services/<slug>. A service is a
`sync_adapters` row; the Automations sidebar lists every one under Services (drafts
too), and published ones appear in Import from… / Send to….
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import timezone
from typing import Any

from flask import Flask, abort, jsonify, redirect, render_template, request, url_for
from flask_babel import gettext
from sqlalchemy.orm import Session

from yaffo.background_tasks.tasks.generate_adapter import generate_adapter_task
from yaffo.db import db
from yaffo.db.models import (
    AUTOMATION_STATUS_IN_PROGRESS, AUTOMATION_STATUS_READY, CONVERSATION_TYPE_PERMISSION, CONVERSATION_TYPE_TEST,
    CONVERSATION_TYPE_USER,
    SyncAdapter,
)
from yaffo.db.repositories import service_builder_repository as services
from yaffo.db.repositories import sync_repository
from yaffo.logging_config import get_logger
from yaffo.routes.utilities.common import automations_sidebar_context
from yaffo.routes.utilities.sync_account import SERVICE_TEST_ACCOUNT
from yaffo.routes.utilities.sync_views import error_text
from yaffo.site_agents import llm_config
from yaffo.sync import access, connections, credentials, oauth, oauth1, secrets_store
from yaffo.sync.adapter_static import analyze_adapter
from yaffo.sync.adapter_test import run_adapter_test
from yaffo.sync.adapters import AdapterUnavailable, LoadedAdapter, load_adapter, load_working
from yaffo.sync.options import OPTION_REMOTE_COLLECTION, OPTION_REMOTE_FOLDER
from yaffo.sync.providers import AUTH_OAUTH1, AUTH_OAUTH2, InstanceUrlError, normalize_instance_url

logger = get_logger(__name__, 'webapp')

# A pending OAuth sign-in for a service's test account names the service this way
# (where a sync's sign-in names its automation); see sync_account.sync_oauth_return.
TEST_ACCOUNT_MARKER = SERVICE_TEST_ACCOUNT


@dataclass(frozen=True)
class Endpoint:
    method: str
    url: str
    risk: str
    followup: bool


@dataclass
class ServicePage:
    row: SyncAdapter
    draft: LoadedAdapter | None
    problems: list[str] = field(default_factory=list)
    endpoints: list[Endpoint] = field(default_factory=list)
    test_account: str | None = None
    test_account_problem: bool = False
    used_by: int = 0
    regenerate: bool = False
    return_address: str | None = None

    @property
    def provider(self):
        return self.draft.preset if self.draft else None

    @property
    def in_progress(self) -> bool:
        return self.row.builder_status == AUTOMATION_STATUS_IN_PROGRESS

    @property
    def needs_app(self) -> bool:
        """The user registers their own app with the service: always for OAuth 1, for
        OAuth 2 when the block lists app_credentials."""
        metadata = self.draft.metadata if self.draft else {}
        if self.provider is None:
            return False
        if self.provider.auth_kind == AUTH_OAUTH1:
            return True
        return self.provider.auth_kind == AUTH_OAUTH2 and bool((metadata.get("connection") or {}).get("app_credentials"))

    @property
    def app_labels(self) -> dict[str, str]:
        """The service's own words for its app form ("Key", "Secret", "Callback URL"),
        from the block's app_form_labels, else Yaffo's."""
        labels = ((self.draft.metadata.get("connection") or {}).get("app_form_labels") or {}) if self.draft else {}
        return {"client_id": str(labels.get("client_id") or gettext("Client ID")),
                "client_secret": str(labels.get("client_secret") or gettext("Client secret")),
                "redirect": str(labels.get("redirect") or gettext("redirect address"))}

    @property
    def app_saved(self) -> bool:
        return bool(self.row.app_secret_ref)

    @property
    def can_publish(self) -> bool:
        return self.draft is not None and not self.problems and not self.in_progress \
            and services.has_unpublished_draft(self.row)

    @property
    def remote_options(self) -> list[str]:
        if self.draft is None:
            return []
        return [k for k, spec in self.draft.options.items()
                if spec.get("type") in (OPTION_REMOTE_FOLDER, OPTION_REMOTE_COLLECTION)]


def _uses(session: Session, adapter_id: int) -> int:
    return len(sync_repository.syncs_using_adapter(session, adapter_id))


def build_service_page(session: Session, row: SyncAdapter, port: int | None = None) -> ServicePage:
    draft = None
    problems: list[str] = []
    if row.working_code:
        try:
            draft = load_working(session, row.id)
        except AdapterUnavailable:
            problems = [str(p) for p in analyze_adapter(row.working_code).problems] \
                or [gettext("The draft can't run yet.")]
    page = ServicePage(row=row, draft=draft, problems=problems, used_by=_uses(session, row.id))
    if row.working:
        page.endpoints = [Endpoint(e["method"], e["url_pattern"] or gettext("(a link the service returned)"),
                                   e["risk"], e["grant_kind"] != "endpoint")
                          for e in row.working.get("manifest") or []]
    connection = connections.get(session, row.test_connection_key)
    if connection is not None:
        page.test_account = connection.account_label or connection.account_id
        page.test_account_problem = connection.status != connections.CONNECTION_READY
    if row.published:
        try:
            load_adapter(session, row.id)
        except AdapterUnavailable:
            page.regenerate = True
    provider = page.provider
    if provider is not None and provider.is_oauth and port and \
            ((draft.metadata.get("connection") or {}).get("redirect") == "registered"):
        page.return_address = oauth.loopback_redirect_uri(provider, port)
    return page


def _service_error_text(code: str | None) -> str | None:
    if not code:
        return None
    return {
        "draft_not_ready": gettext("The draft doesn't pass its check yet, so its sign-in isn't known."),
        "app_missing": gettext("Save the app credentials first."),
        "client_id_missing": gettext("Enter the client ID."),
        "publish_refused": gettext("There's no passing draft to publish."),
        "service_in_use": gettext("A sync still uses this service. Delete that sync first."),
    }.get(code) or error_text(code) or gettext("That didn't work. Try again.")


@dataclass(frozen=True)
class DisplayMessage:
    type: str
    content: str


def display_message(message) -> DisplayMessage:
    """A conversation entry as the page first renders it: a permission request as a
    sentence (the page's script then draws it as a card with its buttons)."""
    if message.type != CONVERSATION_TYPE_PERMISSION:
        return DisplayMessage(message.type, message.content)
    try:
        payload = json.loads(message.content)
    except ValueError:
        payload = {}
    status = payload.get("status")
    if payload.get("kind") == services.PERMISSION_LIVE_READS:
        text = (gettext("The builder asks to make read-only calls to %(account)s while building.",
                        account=payload.get("account", "")) if status == services.PENDING
                else gettext("Live read-only tests allowed for %(account)s.", account=payload.get("account", ""))
                if status == services.ALLOWED else gettext("Live read-only tests not allowed."))
    else:
        domains = ", ".join(payload.get("domains") or {})
        text = (gettext("The builder asks to read pages from: %(domains)s", domains=domains) if status == services.PENDING
                else gettext("Allowed: %(domains)s", domains=", ".join(payload.get("allowed") or []))
                if status in (services.ALLOWED, services.PARTIAL)
                else gettext("Not allowed: %(domains)s", domains=domains))
    return DisplayMessage(message.type, text)


def _require_service(slug: str) -> SyncAdapter:
    row = services.get_service(db.session, slug)
    if row is None:
        abort(404)
    return row


def _port() -> int:
    try:
        return int(request.environ.get("SERVER_PORT") or request.host.rsplit(":", 1)[-1])
    except ValueError:
        return 80


def _back(row: SyncAdapter, tab: str | None = None, **params: str):
    return redirect(url_for("sync_service", slug=row.slug, tab=tab, **params))


def _start(row: SyncAdapter, message: str) -> None:
    services.add_message(db.session, row.id, CONVERSATION_TYPE_USER, message)
    services.set_builder_status(db.session, row.id, AUTOMATION_STATUS_IN_PROGRESS)
    generate_adapter_task(row.id, message)


def _test_options(page: ServicePage, form: Any) -> dict[str, Any]:
    """Option values from the Test form: remote options as {id, label}."""
    options: dict[str, Any] = {}
    if page.draft is None:
        return options
    for key, spec in page.draft.options.items():
        raw = (form.get(f"option_{key}") or "").strip()
        if not raw:
            continue
        if spec.get("type") in (OPTION_REMOTE_FOLDER, OPTION_REMOTE_COLLECTION):
            options[key] = {"id": raw, "label": raw}
        elif spec.get("type") == "bool":
            options[key] = raw in ("1", "true", "on")
        elif spec.get("type") == "int":
            try:
                options[key] = int(raw)
            except ValueError:
                continue
        else:
            options[key] = raw
    return options


def init_sync_service_routes(app: Flask) -> None:
    @app.route("/utilities/automations/services/new", methods=["POST"])
    def sync_service_new():
        name = (request.form.get("name") or "").strip()
        if not name:
            abort(400)
        row = services.create_service(db.session, name)
        return redirect(url_for("sync_service", slug=row.slug))

    @app.route("/utilities/automations/services/<slug>", methods=["GET"])
    def sync_service(slug: str):
        row = _require_service(slug)
        page = build_service_page(db.session, row, _port())
        return render_template(
            "utilities/sync_service.html", service=page, row=row,
            messages=[display_message(m) for m in services.messages(db.session, row.id)],
            live_reads=services.live_reads_allowed(row),
            tab=request.args.get("tab") or "overview",
            service_error=_service_error_text(request.args.get("service_error")),
            notice=request.args.get("notice"),
            **automations_sidebar_context(),
        )

    @app.route("/utilities/automations/services/<slug>/chat", methods=["POST"])
    def sync_service_chat(slug: str):
        row = _require_service(slug)
        if row.builder_status == AUTOMATION_STATUS_IN_PROGRESS:
            return jsonify({"error": gettext("The builder is already working."), "code": "generation_already_running"}), 409
        message = ((request.get_json(silent=True) or {}).get("message") or "").strip()
        if not message:
            return jsonify({"error": gettext("Message is required."), "code": "message_required"}), 400
        if llm_config.selected_key_missing():
            return jsonify({"error": gettext("No API key configured. Add your %(provider)s API key in Settings → AI Generation.",
                                             provider=llm_config.selected_provider_label()),
                            "code": "api_key_missing"}), 400
        _start(row, message)
        return jsonify({"slug": slug}), 202

    @app.route("/utilities/automations/services/<slug>/status", methods=["GET"])
    def sync_service_status(slug: str):
        row = _require_service(slug)
        entries = services.messages(db.session, row.id)
        started = next((m.created_at for m in reversed(entries) if m.type == CONVERSATION_TYPE_USER), None)
        return jsonify({
            "slug": slug, "status": row.builder_status or AUTOMATION_STATUS_READY,
            "started_at": started.replace(tzinfo=timezone.utc).isoformat() if started else None,
            "working_code": row.working_code, "published_code": row.published_code,
            "messages": [{"id": m.id, "type": m.type, "content": m.content} for m in entries],
        })

    @app.route("/utilities/automations/services/<slug>/cancel", methods=["POST"])
    def sync_service_cancel(slug: str):
        row = _require_service(slug)
        services.set_builder_status(db.session, row.id, AUTOMATION_STATUS_READY)
        return "", 204

    @app.route("/utilities/automations/services/<slug>/test", methods=["POST"])
    def sync_service_test(slug: str):
        """The user's Test: reads live with the test account, writes never sent. The
        result is kept on the conversation, so the builder sees it next time."""
        row = _require_service(slug)
        page = build_service_page(db.session, row)
        report = run_adapter_test(db.session, row.id, _test_options(page, request.form), live=True)
        services.add_message(db.session, row.id, CONVERSATION_TYPE_TEST, report.model_text())
        return render_template("utilities/_service_test_result.html", report=report)

    @app.route("/utilities/automations/services/<slug>/app", methods=["POST"])
    def sync_service_app(slug: str):
        """The user's own OAuth app for this service (client id and secret), kept in the
        keychain under the service and reused by every sync that uses it."""
        row = _require_service(slug)
        client_id = (request.form.get("client_id") or "").strip()
        client_secret = (request.form.get("client_secret") or "").strip()
        if not client_id:
            return _back(row, "account", service_error="client_id_missing")
        ref = row.app_secret_ref or f"sync.app.{row.slug}"
        secrets_store.store_secret(ref, {"client_id": client_id, "client_secret": client_secret or None})
        row.app_secret_ref = ref
        db.session.commit()
        return _back(row, "account", notice="app_saved")

    @app.route("/utilities/automations/services/<slug>/test-account", methods=["POST"])
    def sync_service_test_account(slug: str):
        """Sign in the account the builder's Test uses: with a key or password here, or
        through the service's own sign-in page for OAuth."""
        row = _require_service(slug)
        page = build_service_page(db.session, row)
        provider = page.provider
        if provider is None:
            return _back(row, "account", service_error="draft_not_ready")
        if provider.is_oauth:
            if page.needs_app and not page.app_saved:
                return _back(row, "account", service_error="app_missing")
            return_to = request.host_url.rstrip("/") + url_for("sync_service", slug=row.slug, tab="account")
            if not provider.is_oauth2:
                try:
                    return redirect(oauth1.begin_sign_in(db.session, provider, _port(), return_to,
                                                         TEST_ACCOUNT_MARKER + row.slug, http=access.make_http()))
                except oauth.SignInError as e:
                    return _back(row, "account", service_error=e.code)
            return redirect(oauth.begin_sign_in(db.session, provider, list(provider.base_scopes), _port(), return_to,
                                                TEST_ACCOUNT_MARKER + row.slug))
        instance = None
        if provider.instance:
            try:
                instance = {"base_url": normalize_instance_url(request.form.get("base_url") or "",
                                                               allow_http=request.form.get("allow_http") == "1")}
            except InstanceUrlError as e:
                return _back(row, "account", service_error=e.code)
        try:
            secret = credentials.secret_from_form(provider, request.form)
            key = credentials.sign_in_with_secret(db.session, provider, secret, instance, http=access.make_http())
        except oauth.SignInError as e:
            return _back(row, "account", service_error=e.code)
        row.test_connection_key = key
        db.session.commit()
        return _back(row, "account")

    @app.route("/utilities/automations/services/<slug>/permissions/<int:message_id>", methods=["POST"])
    def sync_service_permission(slug: str, message_id: int):
        """The user's answer to one of the builder's requests (read docs from new
        domains, read the test account live). The builder continues with it."""
        row = _require_service(slug)
        body = request.get_json(silent=True) or {}
        domains = body.get("domains")
        sentence = services.resolve_permission(db.session, row.id, message_id, body.get("decision") == "allow",
                                               [d for d in domains if isinstance(d, str)] if isinstance(domains, list) else None)
        if sentence is None:
            return jsonify({"error": gettext("That request was already answered."), "code": "permission_not_pending"}), 409
        started = row.builder_status != AUTOMATION_STATUS_IN_PROGRESS and not llm_config.selected_key_missing()
        if started:
            _start(row, sentence)
        return jsonify({"started": started})

    @app.route("/utilities/automations/services/<slug>/live-reads/revoke", methods=["POST"])
    def sync_service_revoke_live_reads(slug: str):
        row = _require_service(slug)
        row.live_reads_key = None
        db.session.commit()
        return _back(row, "account")

    @app.route("/utilities/automations/services/<slug>/publish", methods=["POST"])
    def sync_service_publish(slug: str):
        row = _require_service(slug)
        page = build_service_page(db.session, row)
        if not page.can_publish or not services.publish(db.session, row.id):
            return _back(row, service_error="publish_refused")
        return _back(row, notice="published")

    @app.route("/utilities/automations/services/<slug>/regenerate", methods=["POST"])
    def sync_service_regenerate(slug: str):
        """Ask the builder to rewrite a service that no longer passes the current
        adapter contract (after an app upgrade)."""
        row = _require_service(slug)
        if row.builder_status == AUTOMATION_STATUS_IN_PROGRESS:
            return _back(row)
        problems = [str(p) for p in analyze_adapter(row.published_code or row.working_code or "").problems]
        _start(row, "Regenerate this service: it no longer passes the adapter contract. Keep what it does and "
                    "its direction; fix these problems:\n" + "\n".join(f"- {p}" for p in problems or ["(none found)"]))
        return _back(row)

    @app.route("/utilities/automations/services/<slug>/delete", methods=["POST"])
    def sync_service_delete(slug: str):
        row = _require_service(slug)
        if _uses(db.session, row.id):
            return _back(row, service_error="service_in_use")
        test_key = row.test_connection_key
        if row.app_secret_ref:
            secrets_store.delete_secret(row.app_secret_ref)
        db.session.delete(row)
        db.session.commit()
        connections.release_if_unreferenced(db.session, test_key)
        return redirect(url_for("automations_index"))
