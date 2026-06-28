"""Automations utility: list/build custom automations under /utilities/automations.

A first-class utility (sidebar panel in templates/utilities/_base.html). The detail
page renders inside the utilities layout (utility_content). A chat request records
the prompt, flips the automation to IN_PROGRESS, and enqueues
generate_automation_task; the run lives on the automation, so the browser polls
/status. Publish copies working_code -> published_code. System automations are
code-backed built-ins: read-only chat, can't be deleted.
"""
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone

from flask import Flask, abort, jsonify, make_response, redirect, render_template, request, url_for
from flask_babel import gettext, ngettext

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
    JOB_STATUS_PENDING,
    JOB_STATUS_RUNNING,
    TRIGGER_TYPE_EVENT,
)
from yaffo.db.repositories import automation_repository as repo
from yaffo.db.repositories import media_dir_repository
from yaffo.db.repositories import media_repository
from yaffo.distance_units import (
    distance_to_kilometers,
    get_saved_distance_unit,
    kilometers_to_distance,
    normalize_distance_unit,
    supported_distance_unit_options,
)
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
    status_label: str
    is_finished: bool
    is_error: bool
    progress: int          # 0–100; shown for in-progress runs
    started_at: datetime | None
    finished_at: datetime | None
    summary: str
    error: str | None


@dataclass(frozen=True)
class RouteError:
    error: str
    code: str


@dataclass(frozen=True)
class AutomationMessagePayload:
    type: str
    content: str


@dataclass(frozen=True)
class AutomationStatusPayload:
    slug: str
    status: str
    started_at: str | None
    working_code: str | None
    published_code: str | None
    messages: list[AutomationMessagePayload]


@dataclass(frozen=True)
class AutomationRunStarted:
    slug: str
    media_count: int | None


def _run_progress(job: Job) -> int:
    """Percent complete (0–100) — processed (done + errored + cancelled) over the
    task count, matching the live job card's math."""
    if not job.task_count or job.task_count <= 0:
        return 0
    processed = (job.completed_count or 0) + (job.error_count or 0) + (job.cancelled_count or 0)
    return min(100, int(processed / job.task_count * 100))


def _run_label(job: Job) -> str:
    if job.automation is not None and job.automation.is_system:
        return job.automation.display_name
    label = job.message or job.name
    return {
        "Imported {totalCount}/{taskCount} photos": gettext("Import photos"),
        "Indexed {totalCount}/{taskCount} photos": gettext("Index photos"),
        "Processed {totalCount}/{taskCount} media items": gettext("Find duplicates"),
        "import_photos": gettext("Import photos"),
        "index_photos": gettext("Index photos"),
        "find_duplicates": gettext("Find duplicates"),
    }.get(label, label)


def _run_summary(job: Job) -> str:
    """One-line result for a run: progress counts for batch jobs (find_duplicates /
    index), else the job's message (custom runs carry the automation name)."""
    completed = job.completed_count or 0
    errors = job.error_count or 0
    cancelled = job.cancelled_count or 0
    if job.task_count and job.task_count > 1:
        summary = gettext(
            "%(completed)s of %(total)s processed",
            completed=completed,
            total=job.task_count,
        )
        if errors:
            summary += ", " + ngettext(
                "%(count)s error",
                "%(count)s errors",
                errors,
                count=errors,
            )
        if cancelled:
            summary += ", " + ngettext(
                "%(count)s cancelled",
                "%(count)s cancelled",
                cancelled,
                count=cancelled,
            )
        return summary
    return _run_label(job)


def _run_status_label(status: str) -> str:
    return {
        JOB_STATUS_PENDING: gettext("Pending"),
        JOB_STATUS_RUNNING: gettext("Running"),
        JOB_STATUS_COMPLETED: gettext("Completed"),
        JOB_STATUS_CANCELLED: gettext("Cancelled"),
        JOB_STATUS_FAILED: gettext("Failed"),
    }.get(status, status.capitalize())


def _run_view(job: Job) -> AutomationRunView:
    return AutomationRunView(
        status=job.status,
        status_label=_run_status_label(job.status),
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
    def _error(message: str, code: str, status: int):
        return jsonify(asdict(RouteError(error=message, code=code))), status

    def _distance_config_value(automation: Automation, field) -> tuple[float, str]:
        config = automation.config or {}
        preferred_unit = get_saved_distance_unit(db.session)
        unit = normalize_distance_unit(config.get(field.unit_key or "")) or preferred_unit
        raw_value = config.get(field.key)
        if isinstance(raw_value, (int, float)):
            return raw_value, unit
        stored_kilometers = config.get(f"{field.key}_kilometers")
        if isinstance(stored_kilometers, (int, float)):
            return round(kilometers_to_distance(float(stored_kilometers), unit), 2), unit
        return field.default, unit

    def _localized_event_labels() -> dict[str, str]:
        return {
            event_type: {
                "Media imported": gettext("Media imported"),
                "Media indexed": gettext("Media indexed"),
                "Duplicates found": gettext("Duplicates found"),
                "Media modified": gettext("Media modified"),
                "Media labeled": gettext("Media labeled"),
            }.get(label, label)
            for event_type, label in EVENTS.items()
        }

    def _config_fields(automation: Automation | None) -> list[dict]:
        """The Configure-modal context: each declared field plus its live value."""
        if automation is None:
            return []
        fields = []
        for f in config_fields_for(automation):
            field = {
                "key": f.key, "label": str(f.label),
                "help": str(f.help) if f.help is not None else None,
                "min": f.min, "max": f.max, "step": f.step, "type": f.type,
                "required": f.required,
                "value": config_value(automation, f)
            }
            if f.type == "distance":
                value, unit = _distance_config_value(automation, f)
                field.update({
                    "unit": unit,
                    "unit_key": f.unit_key,
                    "units": supported_distance_unit_options(),
                    "value": value,
                })
            fields.append(field)
        return fields

    def _supports_scoped_run(automation: Automation) -> bool:
        """Whether Run-now scopes to a user-picked file/folder (the "Run on a
        folder…/file…" actions) vs the plain context-less "Run now".

        Driven by the automation's configured triggers: when **every** trigger is an
        event (so the automation is purely photo-driven), running it manually means
        "run it over these photos" — show the path picker. If any trigger is a
        schedule, or there are no triggers, it gets the whole-library Run-now instead."""
        triggers = automation.triggers
        return bool(triggers) and all(t.trigger_type == TRIGGER_TYPE_EVENT for t in triggers)

    def _render_page(selected_slug: str | None):
        selected = repo.get_by_slug(db.session, selected_slug) if selected_slug else None
        media_dirs = media_dir_repository.list_media_dirs(db.session)
        return render_template(
            "utilities/automations.html",
            selected=selected,
            selected_slug=selected_slug,
            # Default the file/folder picker to the user's first media dir (their library)
            # rather than home; None when none is configured.
            default_media_dir=(media_dirs[0]["path"] if media_dirs else None),
            config_fields=_config_fields(selected),
            recent_runs=_recent_runs(selected),
            scoped_run=_supports_scoped_run(selected) if selected else False,
            **automations_sidebar_context(),
        )

    def _triggers_context(automation: Automation, error: str | None = None) -> dict:
        return {
            "triggers": sorted(automation.triggers, key=lambda t: t.id),
            "slug": automation.slug,
            "events": _localized_event_labels(),
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
        payload = AutomationStatusPayload(
            slug=automation.slug,
            status=automation.status,
            # created_at is naive UTC (datetime.utcnow); stamp it so the browser's
            # Date parser reads it as UTC, not local — the chat's elapsed counter
            # subtracts it from Date.now(). (Mirrors site_agents serializers._utc_iso.)
            started_at=started_at.replace(tzinfo=timezone.utc).isoformat() if started_at else None,
            working_code=automation.working_code,
            published_code=automation.published_code,
            messages=[AutomationMessagePayload(type=m.type, content=m.content) for m in automation.messages],
        )
        return asdict(payload)

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
            return _error(gettext("Automation name is required"), "automation_name_required", 400)
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

    @app.route("/utilities/automations/<slug>/edit", methods=["GET"])
    def automations_edit(slug: str):
        """The custom automation builder screen: conversation plus generated code.

        Kept separate from the automation detail page so the default automation view
        stays focused on run history and operational controls."""
        automation = repo.get_by_slug(db.session, slug)
        if automation is None or automation.is_system:
            abort(404)
        media_dirs = media_dir_repository.list_media_dirs(db.session)
        return render_template(
            "utilities/automations_edit.html",
            selected=automation,
            selected_slug=slug,
            default_media_dir=(media_dirs[0]["path"] if media_dirs else None),
            selected_status=automation.status,
            selected_has_draft=(
                automation.status == AUTOMATION_STATUS_READY
                and automation.working_code is not None
            ),
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
            return _error(gettext("System automations cannot be edited."), "system_automation_locked", 400)
        name = (request.form.get("name") or "").strip()
        if not name:
            return _error(gettext("Automation name is required"), "automation_name_required", 400)
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
            return _error(
                gettext("This automation has no configurable settings."),
                "automation_not_configurable",
                400,
            )
        config = dict(automation.config or {})
        for field in fields:
            raw = (request.form.get(field.key) or "").strip()
            if field.type == "bool":
                value = raw in ("on", "true", "True")
            elif field.type == "distance":
                if field.unit_key is None:
                    raise NotImplementedError("distance fields require unit_key")
                try:
                    value = float(raw)
                except ValueError:
                    return _error(
                        gettext("%(label)s must be a number.", label=str(field.label)),
                        "config_value_not_numeric",
                        400,
                    )
                if (field.min is not None and value < field.min) or \
                        (field.max is not None and value > field.max):
                    return _error(
                        gettext(
                            "%(label)s must be between %(min)s and %(max)s.",
                            label=str(field.label),
                            min=field.min,
                            max=field.max,
                        ),
                        "config_value_out_of_range",
                        400,
                    )
                unit = normalize_distance_unit(request.form.get(field.unit_key))
                if unit is None:
                    return _error(
                        gettext("Unsupported distance unit"),
                        "unsupported_distance_unit",
                        400,
                    )
                config[field.unit_key] = unit
                config[f"{field.key}_kilometers"] = distance_to_kilometers(value, unit)
            elif field.type in ("float", "int"):
                try:
                    value = float(raw) if field.type == "float" else int(raw)
                except ValueError:
                    return _error(
                        gettext("%(label)s must be a number.", label=str(field.label)),
                        "config_value_not_numeric",
                        400,
                    )
                if (field.min is not None and value < field.min) or \
                        (field.max is not None and value > field.max):
                    return _error(
                        gettext(
                            "%(label)s must be between %(min)s and %(max)s.",
                            label=str(field.label),
                            min=field.min,
                            max=field.max,
                        ),
                        "config_value_out_of_range",
                        400,
                    )
            elif field.type == "string":
                if field.required and not raw:
                    return _error(
                        gettext("%(label)s is required.", label=str(field.label)),
                        "config_value_required",
                        400,
                    )
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
            return _error(gettext("System automations cannot be deleted"), "system_automation_locked", 400)
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
            media_item_ids = media_repository.get_media_item_ids_under_path(db.session, path)
            if not media_item_ids:
                return _error(
                    gettext("No indexed photos found under that path."),
                    "indexed_photos_not_found",
                    400,
                )
            context = EventContext(event_type=MANUAL_RUN_EVENT_TYPE, media_item_ids=media_item_ids)

        if not invoke_automation(automation, context):
            return _error(
                gettext("Nothing to run yet — publish the automation's code first."),
                "automation_not_runnable",
                400,
            )
        return jsonify(asdict(AutomationRunStarted(
            slug=slug,
            media_count=len(context.media_item_ids) if context else None,
        ))), 202

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
                error = gettext("Enter a valid 5-field cron expression (e.g. */30 * * * *).")
            elif trigger is not None:
                trigger.cron = cron
                trigger.next_run_at = None
                db.session.commit()
            else:
                repo.add_schedule_trigger(db.session, slug, cron)
        elif action == "add_event":
            event_type = (request.form.get("new_event_type") or "").strip()
            if event_type not in EVENTS:
                error = gettext("Choose an event type.")
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
            return _error(gettext("Unknown trigger action."), "unknown_trigger_action", 400)

        return _render_triggers(automation, error)

    @app.route("/utilities/automations/<slug>/chat", methods=["POST"])
    def automations_chat(slug: str):
        automation = repo.get_by_slug(db.session, slug)
        if automation is None:
            abort(404)
        if automation.is_system:
            return _error(gettext("System automations cannot be edited."), "system_automation_locked", 400)
        if automation.status == AUTOMATION_STATUS_IN_PROGRESS:
            return _error(gettext("A generation is already running."), "generation_already_running", 409)
        message = (request.get_json(silent=True) or {}).get("message", "").strip()
        if not message:
            return _error(gettext("Message is required."), "message_required", 400)
        if llm_config.selected_key_missing():
            return _error(
                gettext(
                    "No API key configured. Add your %(provider)s API key in Settings → AI Generation.",
                    provider=llm_config.selected_provider_label(),
                ),
                "api_key_missing",
                400,
            )

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
        not performed, so nothing changes -- the run's media_item_ids are the matched
        photos."""
        automation = repo.get_by_slug(db.session, slug)
        if automation is None:
            abort(404)
        if not (automation.working_code or automation.published_code):
            return _error(gettext("No code to test yet."), "automation_code_missing", 400)
        body = request.get_json(silent=True) or {}
        path = (body.get("path") or "").strip()
        if not path:
            return _error(gettext("No path selected."), "path_required", 400)
        version = body.get("version")  # which code panel view to test: working | published
        media_item_ids = media_repository.get_media_item_ids_under_path(db.session, path)
        result = preview_automation(db.session, automation, media_item_ids=media_item_ids, version=version)
        return jsonify(result.to_dict())

    @app.route("/utilities/automations/<slug>/publish", methods=["POST"])
    def automations_publish(slug: str):
        if repo.get_by_slug(db.session, slug) is None:
            abort(404)
        if not repo.publish(db.session, slug):
            return _error(gettext("No draft to publish."), "draft_missing", 409)
        return _hx_refresh()

    @app.route("/utilities/automations/<slug>/discard", methods=["POST"])
    def automations_discard(slug: str):
        if repo.get_by_slug(db.session, slug) is None:
            abort(404)
        repo.discard_draft(db.session, slug)
        return _hx_refresh()
