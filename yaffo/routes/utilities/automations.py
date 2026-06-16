"""Automations utility: list/build custom automations under /utilities/automations.

A first-class utility (sidebar panel in templates/utilities/_base.html). The detail
page renders inside the utilities layout (utility_content). A chat request records
the prompt, flips the automation to IN_PROGRESS, and enqueues
generate_automation_task; the run lives on the automation, so the browser polls
/status. Publish copies working_code -> published_code. System automations are
code-backed built-ins: read-only chat, can't be deleted.
"""
import re

from flask import Flask, abort, jsonify, make_response, redirect, render_template, request, url_for

from yaffo.background_tasks.automation_sandbox.preview import preview_automation
from yaffo.background_tasks.schedule import is_valid_cron
from yaffo.background_tasks.tasks.generate_automation import generate_automation_task
from yaffo.db import db
from yaffo.db.models import (
    Automation,
    AutomationTrigger,
    AUTOMATION_STATUS_ACCEPTED,
    AUTOMATION_STATUS_IN_PROGRESS,
    AUTOMATION_STATUS_READY,
    CONVERSATION_TYPE_USER,
    EVENTS,
    TRIGGER_TYPE_EVENT,
    TRIGGER_TYPE_SCHEDULE,
)
from yaffo.db.repositories import automation_repository as repo
from yaffo.db.repositories import photos_repository
from yaffo.page_builder import llm_config
from yaffo.routes.utilities.common import automations_sidebar_context

_MAX_BASE_SLUG_LENGTH = 30


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    if not re.match(r"^[a-z]", slug):
        slug = f"automation-{slug}" if slug else "automation"
    return slug[:_MAX_BASE_SLUG_LENGTH].rstrip("-")


def _unique_slug(name: str) -> str:
    base = _slugify(name)
    slug, counter = base, 2
    while repo.get_by_slug(db.session, slug) is not None:
        slug = f"{base}-{counter}"
        counter += 1
    return slug


def init_automations_routes(app: Flask):
    def _render_page(selected_slug: str | None):
        selected = repo.get_by_slug(db.session, selected_slug) if selected_slug else None
        return render_template(
            "utilities/automations.html",
            selected=selected,
            selected_slug=selected_slug,
            selected_status=selected.status if selected else None,
            selected_has_draft=(
                selected is not None
                and selected.status == AUTOMATION_STATUS_READY
                and selected.working_code is not None
            ),
            **automations_sidebar_context(),
        )

    def _triggers_context(automation: Automation, error: str | None = None) -> dict:
        return {
            "triggers": sorted(automation.triggers, key=lambda t: t.id),
            "slug": automation.slug,
            "events": EVENTS,
            "error": error,
        }

    def _render_triggers(automation: Automation, error: str | None = None):
        return render_template(
            "utilities/automations_triggers.html", **_triggers_context(automation, error)
        )

    def _find_trigger(automation: Automation, raw_id) -> AutomationTrigger | None:
        try:
            trigger_id = int(raw_id)
        except (TypeError, ValueError):
            return None
        return next((t for t in automation.triggers if t.id == trigger_id), None)

    def _hx_refresh():
        response = make_response("", 204)
        response.headers["HX-Refresh"] = "true"
        return response

    def _status_payload(automation: Automation) -> dict:
        started_at = next(
            (m.created_at for m in reversed(automation.messages) if m.type == CONVERSATION_TYPE_USER),
            None,
        )
        return {
            "slug": automation.slug,
            "status": automation.status,
            "started_at": started_at.isoformat() if started_at else None,
            "working_code": automation.working_code,
            "published_code": automation.published_code,
            "messages": [{"type": m.type, "content": m.content} for m in automation.messages],
        }

    @app.route("/utilities/automations", methods=["GET"])
    def automations_index():
        first = db.session.query(Automation).order_by(Automation.name).first()
        if first is not None:
            return redirect(url_for("automations_show", slug=first.slug))
        return _render_page(None)

    @app.route("/utilities/automations/<slug>", methods=["GET"])
    def automations_show(slug: str):
        if repo.get_by_slug(db.session, slug) is None:
            abort(404)
        return _render_page(slug)

    @app.route("/utilities/automations/create", methods=["POST"])
    def automations_create():
        name = (request.form.get("name") or "").strip()
        if not name:
            return jsonify({"error": "Automation name is required"}), 400
        slug = _unique_slug(name)
        db.session.add(Automation(
            slug=slug, name=name, is_system=False, enabled=False,
            status=AUTOMATION_STATUS_ACCEPTED,
        ))
        db.session.commit()
        return redirect(url_for("automations_show", slug=slug))

    @app.route("/utilities/automations/<slug>/triggers/edit", methods=["GET"])
    def automations_edit_triggers(slug: str):
        """The full trigger editor on its own screen (the detail page only shows a
        read-only summary), so the cron builder has room to breathe."""
        automation = repo.get_by_slug(db.session, slug)
        if automation is None:
            abort(404)
        return render_template(
            "utilities/automations_triggers_edit.html",
            selected=automation,
            selected_slug=slug,
            **_triggers_context(automation),
            **automations_sidebar_context(),
        )

    @app.route("/utilities/automations/<slug>/details", methods=["POST"])
    def automations_update_details(slug: str):
        """Rename / re-describe a custom automation. The slug stays fixed (it's the
        stable id in URLs and the sidebar), so only the display name and description
        change. System automations are route-locked like elsewhere."""
        automation = repo.get_by_slug(db.session, slug)
        if automation is None:
            abort(404)
        if automation.is_system:
            return jsonify({"error": "System automations cannot be edited."}), 400
        name = (request.form.get("name") or "").strip()
        if not name:
            return jsonify({"error": "Automation name is required"}), 400
        automation.name = name
        automation.description = (request.form.get("description") or "").strip() or None
        db.session.commit()
        return redirect(url_for("automations_show", slug=slug))

    @app.route("/utilities/automations/<slug>/delete", methods=["POST"])
    def automations_delete(slug: str):
        automation = repo.get_by_slug(db.session, slug)
        if automation is None:
            abort(404)
        if automation.is_system:
            return jsonify({"error": "System automations cannot be deleted"}), 400
        db.session.delete(automation)
        db.session.commit()
        return redirect(url_for("automations_index"))

    @app.route("/utilities/automations/<slug>/enabled", methods=["POST"])
    def automations_toggle_enabled(slug: str):
        automation = repo.get_by_slug(db.session, slug)
        if automation is None:
            abort(404)
        automation.enabled = not automation.enabled
        db.session.commit()
        return _hx_refresh()

    @app.route("/utilities/automations/validate-cron", methods=["GET"])
    def automations_validate_cron():
        """Authoritative cron check for the editor's Advanced field, so the client can
        disable Save without reimplementing croniter. (Static path outranks the
        `<slug>` rule in Werkzeug routing.)"""
        return jsonify({"valid": is_valid_cron((request.args.get("cron") or "").strip())})

    @app.route("/utilities/automations/<slug>/triggers", methods=["POST"])
    def automations_triggers(slug: str):
        """Single HTMX endpoint for trigger save/add-event/remove/toggle; dispatches
        on the hx-vals action and re-renders the triggers fragment. Allowed on system
        automations too: a schedule is runtime state (the dispatcher reads rows live),
        so users can reschedule built-ins like file_sync."""
        automation = repo.get_by_slug(db.session, slug)
        if automation is None:
            abort(404)
        action = request.form.get("action")
        error = None

        if action == "save_schedule":
            # The cron builder (cron_builder.js) composes the expression client-side
            # and submits it in `cron`; the server is the trust boundary and only
            # validates before persisting. `edit_trigger_id` distinguishes editing an
            # existing schedule from adding one. A new (or rescheduled) trigger leaves
            # next_run_at NULL so the dispatcher re-initialises it from the cron.
            cron = (request.form.get("cron") or "").strip()
            edit_id = request.form.get("edit_trigger_id") or ""
            trigger = _find_trigger(automation, edit_id) if edit_id else None
            if edit_id and trigger is None:
                abort(404)
            if not is_valid_cron(cron):
                error = "Enter a valid 5-field cron expression (e.g. */30 * * * *)."
            elif trigger is not None:
                trigger.cron = cron
                trigger.next_run_at = None
                db.session.commit()
            else:
                db.session.add(AutomationTrigger(
                    automation_id=automation.id, trigger_type=TRIGGER_TYPE_SCHEDULE,
                    enabled=True, cron=cron,
                ))
                db.session.commit()
        elif action == "add_event":
            event_type = (request.form.get("new_event_type") or "").strip()
            if event_type not in EVENTS:
                error = "Choose an event type."
            else:
                db.session.add(AutomationTrigger(
                    automation_id=automation.id, trigger_type=TRIGGER_TYPE_EVENT,
                    enabled=True, event_type=event_type,
                ))
                db.session.commit()
        elif action in ("remove", "toggle"):
            trigger = _find_trigger(automation, request.form.get("trigger_id"))
            if trigger is None:
                abort(404)
            if action == "remove":
                db.session.delete(trigger)
            else:
                trigger.enabled = not trigger.enabled
            db.session.commit()
        else:
            return jsonify({"error": "Unknown trigger action."}), 400

        return _render_triggers(automation, error)

    @app.route("/utilities/automations/<slug>/chat", methods=["POST"])
    def automations_chat(slug: str):
        automation = repo.get_by_slug(db.session, slug)
        if automation is None:
            abort(404)
        if automation.is_system:
            return jsonify({"error": "System automations cannot be edited."}), 400
        if automation.status == AUTOMATION_STATUS_IN_PROGRESS:
            return jsonify({"error": "A generation is already running."}), 409
        message = (request.get_json(silent=True) or {}).get("message", "").strip()
        if not message:
            return jsonify({"error": "Message is required."}), 400
        if llm_config.get_api_key() is None:
            return jsonify({"error": "No API key configured. Add your Anthropic API key in Settings → AI Generation."}), 400

        repo.add_message(db.session, automation.id, CONVERSATION_TYPE_USER, message)
        repo.set_status(db.session, slug, AUTOMATION_STATUS_IN_PROGRESS)
        generate_automation_task(slug, message)
        return jsonify({"slug": slug}), 202

    @app.route("/utilities/automations/<slug>/status", methods=["GET"])
    def automations_status(slug: str):
        automation = repo.get_by_slug(db.session, slug)
        if automation is None:
            abort(404)
        return jsonify(_status_payload(automation))

    @app.route("/utilities/automations/<slug>/cancel", methods=["POST"])
    def automations_cancel(slug: str):
        if repo.get_by_slug(db.session, slug) is None:
            abort(404)
        repo.set_status(db.session, slug, AUTOMATION_STATUS_ACCEPTED)
        return "", 204

    @app.route("/utilities/automations/<slug>/test-files", methods=["POST"])
    def automations_test_files(slug: str):
        """Dry-run the automation against the indexed photos at/under a user-selected
        path (a file or a folder). Records no Job; mutating actions are recorded but
        not performed, so nothing changes -- the run's photo_ids are the matched
        photos."""
        automation = repo.get_by_slug(db.session, slug)
        if automation is None:
            abort(404)
        if not (automation.working_code or automation.published_code):
            return jsonify({"error": "No code to test yet."}), 400
        path = ((request.get_json(silent=True) or {}).get("path") or "").strip()
        if not path:
            return jsonify({"error": "No path selected."}), 400
        photo_ids = photos_repository.get_photo_ids_under_path(db.session, path)
        return jsonify(preview_automation(db.session, automation, photo_ids=photo_ids).to_dict())

    @app.route("/utilities/automations/<slug>/publish", methods=["POST"])
    def automations_publish(slug: str):
        if repo.get_by_slug(db.session, slug) is None:
            abort(404)
        if not repo.publish(db.session, slug):
            return jsonify({"error": "No draft to publish."}), 409
        return _hx_refresh()

    @app.route("/utilities/automations/<slug>/discard", methods=["POST"])
    def automations_discard(slug: str):
        if repo.get_by_slug(db.session, slug) is None:
            abort(404)
        repo.discard_draft(db.session, slug)
        return _hx_refresh()