"""Automations utility: list/build custom automations under /utilities/automations.

A first-class utility (sidebar panel in templates/utilities/_base.html). The detail
page renders inside the utilities layout (utility_content). A chat request records
the prompt, flips the automation to IN_PROGRESS, and enqueues
generate_automation_task; the run lives on the automation, so the browser polls
/status. Publish copies working_code -> published_code. System automations are
code-backed built-ins: read-only chat, can't be deleted.
"""
import re
from dataclasses import dataclass
from datetime import datetime, timezone

from flask import Flask, abort, jsonify, make_response, redirect, render_template, request, url_for

from yaffo.background_tasks.automation_config import config_fields_for, config_value
from yaffo.background_tasks.automation_dispatch import invoke_automation
from yaffo.background_tasks.automation_sandbox.preview import preview_automation
from yaffo.background_tasks.events import EventContext, MANUAL_RUN_EVENT_TYPE
from yaffo.background_tasks.schedule import is_valid_cron
from yaffo.background_tasks.tasks.generate_automation import generate_automation_task
from yaffo.db import db
from yaffo.db.models import (
    Automation,
    AutomationTrigger,
    Job,
    AUTOMATION_STATUS_ACCEPTED,
    AUTOMATION_STATUS_IN_PROGRESS,
    AUTOMATION_STATUS_READY,
    CONVERSATION_TYPE_USER,
    EVENTS,
    JOB_STATUS_CANCELLED,
    JOB_STATUS_COMPLETED,
    JOB_STATUS_FAILED,
    TRIGGER_TYPE_EVENT,
)
from yaffo.db.repositories import automation_repository as repo
from yaffo.db.repositories import photos_repository
from yaffo.site_agents import llm_config
from yaffo.routes.utilities.common import automations_sidebar_context

_MAX_BASE_SLUG_LENGTH = 30

_RUN_FINISHED_STATUSES = (JOB_STATUS_COMPLETED, JOB_STATUS_FAILED, JOB_STATUS_CANCELLED)


@dataclass(frozen=True)
class AutomationRunView:
    """A single row of an automation's run history, rendered on the detail page.
    Built from a Job (runs reuse the Job table) so the template stays dumb and the
    per-run-kind display logic lives in one tested place."""
    status: str
    is_finished: bool
    is_error: bool
    progress: int          # 0–100; shown for in-progress runs
    started_at: datetime | None
    finished_at: datetime | None
    summary: str
    error: str | None


def _run_progress(job: Job) -> int:
    """Percent complete (0–100) — processed (done + errored + cancelled) over the
    task count, matching the live job card's math."""
    if not job.task_count or job.task_count <= 0:
        return 0
    processed = (job.completed_count or 0) + (job.error_count or 0) + (job.cancelled_count or 0)
    return min(100, int(processed / job.task_count * 100))


def _run_summary(job: Job) -> str:
    """One-line result for a run: progress counts for batch jobs (find_duplicates /
    index), else the job's message (custom runs carry the automation name)."""
    completed = job.completed_count or 0
    errors = job.error_count or 0
    cancelled = job.cancelled_count or 0
    if job.task_count and job.task_count > 1:
        summary = f"{completed} of {job.task_count} processed"
        if errors:
            summary += f", {errors} error{'s' if errors != 1 else ''}"
        if cancelled:
            summary += f", {cancelled} cancelled"
        return summary
    return job.message or job.name


def _run_view(job: Job) -> AutomationRunView:
    return AutomationRunView(
        status=job.status,
        is_finished=job.status in _RUN_FINISHED_STATUSES,
        is_error=job.status == JOB_STATUS_FAILED or bool(job.error_count),
        progress=_run_progress(job),
        started_at=job.started_at or job.created_at,
        finished_at=job.completed_at,
        summary=_run_summary(job),
        error=job.error,
    )


def _recent_runs(automation: Automation | None) -> list[AutomationRunView]:
    if automation is None:
        return []
    return [_run_view(j) for j in repo.get_recent_jobs(db.session, automation.id)]


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
    def _config_fields(automation: Automation | None) -> list[dict]:
        """The Configure-modal context: each declared field plus its live value."""
        if automation is None:
            return []
        return [
            {
                "key": f.key, "label": f.label, "help": f.help,
                "min": f.min, "max": f.max, "step": f.step, "type": f.type,
                "required": f.required,
                "value": config_value(automation, f)
            }
            for f in config_fields_for(automation)
        ]

    def _supports_scoped_run(automation: Automation) -> bool:
        """Whether Run-now scopes to a user-picked file/folder (the "Run on a
        folder…/file…" actions) vs the plain context-less "Run now".

        Driven by the automation's configured triggers: when **every** trigger is an
        event (so the automation is purely photo-driven), running it manually means
        "run it over these photos" — show the file/folder pickers. If any trigger is a
        schedule, or there are no triggers, it gets the whole-library Run-now instead."""
        triggers = automation.triggers
        return bool(triggers) and all(t.trigger_type == TRIGGER_TYPE_EVENT for t in triggers)

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
            config_fields=_config_fields(selected),
            recent_runs=_recent_runs(selected),
            scoped_run=_supports_scoped_run(selected) if selected else False,
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
            # created_at is naive UTC (datetime.utcnow); stamp it so the browser's
            # Date parser reads it as UTC, not local — the chat's elapsed counter
            # subtracts it from Date.now(). (Mirrors site_agents serializers._utc_iso.)
            "started_at": started_at.replace(tzinfo=timezone.utc).isoformat() if started_at else None,
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

    @app.route("/utilities/automations/<slug>/config", methods=["POST"])
    def automations_update_config(slug: str):
        """Save a system automation's runtime settings (e.g. the auto-assign-faces
        match threshold) into Automation.config. Allowed on system automations: like
        a schedule, config is runtime state the task reads live, even though the
        automation's code/identity stays route-locked. Fields/bounds come from the
        declared schema (automation_config), which is the trust boundary."""
        automation = repo.get_by_slug(db.session, slug)
        if automation is None:
            abort(404)
        fields = config_fields_for(automation)
        if not fields:
            return jsonify({"error": "This automation has no configurable settings."}), 400
        config = dict(automation.config or {})
        for field in fields:
            raw = (request.form.get(field.key) or "").strip()
            if field.type == "bool":
                value = raw in ("on", "true", "True")
            elif field.type in ("float", "int"):
                try:
                    value = float(raw) if field.type == "float" else int(raw)
                except ValueError:
                    return jsonify({"error": f"{field.label} must be a number."}), 400
                if (field.min is not None and value < field.min) or \
                        (field.max is not None and value > field.max):
                    return jsonify(
                        {"error": f"{field.label} must be between {field.min} and {field.max}."}
                    ), 400
            elif field.type == "string":
                if field.required and not raw:
                    return jsonify({"error": f"{field.label} is required."}), 400
                value = raw
            else:
                raise NotImplementedError(field.type)
            config[field.key] = value
        automation.config = config
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

    @app.route("/utilities/automations/<slug>/runs", methods=["GET"])
    def automations_runs(slug: str):
        """The run-history fragment, re-served for the section's 5s self-poll so
        in-progress runs appear and tick toward completion without a page reload."""
        automation = repo.get_by_slug(db.session, slug)
        if automation is None:
            abort(404)
        return render_template(
            "utilities/automations_runs.html",
            selected=automation,
            recent_runs=_recent_runs(automation),
        )

    @app.route("/utilities/automations/<slug>/run", methods=["POST"])
    def automations_run_now(slug: str):
        """Run the automation now, independent of its triggers and enabled state.

        A per-photo automation sends a `path` (a user-picked file/folder); the run
        executes for real over the indexed photos under it, via an EventContext —
        the live twin of the test-files dry run. A whole-library handler (file_sync
        / duplicate_scan) sends no path and fires context-less, like a schedule tick.
        Either way the run is enqueued async and shows up in Run history once a
        worker records its Job. Returns 400 when a scoped run matches no indexed
        photos, or for a custom automation with nothing published to run."""
        automation = repo.get_by_slug(db.session, slug)
        if automation is None:
            abort(404)

        path = ((request.get_json(silent=True) or {}).get("path") or "").strip()
        context = None
        if path:
            photo_ids = photos_repository.get_photo_ids_under_path(db.session, path)
            if not photo_ids:
                return jsonify({"error": "No indexed photos found under that path."}), 400
            context = EventContext(event_type=MANUAL_RUN_EVENT_TYPE, media_ids=photo_ids)

        if not invoke_automation(automation, context):
            return jsonify({"error": "Nothing to run yet — publish the automation's code first."}), 400
        return jsonify({"slug": slug, "photo_count": len(context.media_ids) if context else None}), 202

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
                repo.add_schedule_trigger(db.session, slug, cron)
        elif action == "add_event":
            event_type = (request.form.get("new_event_type") or "").strip()
            if event_type not in EVENTS:
                error = "Choose an event type."
            else:
                repo.add_event_trigger(db.session, slug, event_type)
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
        body = request.get_json(silent=True) or {}
        path = (body.get("path") or "").strip()
        if not path:
            return jsonify({"error": "No path selected."}), 400
        version = body.get("version")  # which code panel view to test: working | published
        photo_ids = photos_repository.get_photo_ids_under_path(db.session, path)
        result = preview_automation(db.session, automation, photo_ids=photo_ids, version=version)
        return jsonify(result.to_dict())

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