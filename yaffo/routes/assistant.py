"""Routes for the in-app assistant (docs/development/ai-assistant.md).

A turn is started here and answered by assistant_run_task in the background; the
chat UI polls the conversation until the run settles. Change plans a run recorded
are approved, declined and undone here: approval replays the frozen calls in this
request (plans.py). Everything 404s when the assistant is turned off in Settings,
and in demo mode.
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass

from flask import Flask, abort, jsonify, make_response, render_template, request
from flask_babel import gettext

from yaffo.background_tasks.config import task_queue
from yaffo.background_tasks.tasks.assistant_run import assistant_run_task
from yaffo.db import db
from yaffo.db.models import ASSISTANT_EVENT_USER, ASSISTANT_STATUS_IDLE, ASSISTANT_STATUS_RUNNING
from yaffo.db.repositories import assistant_repository as repo
from yaffo.runtime_mode import demo_mode_enabled
from yaffo.site_agents.assistant import plans
from yaffo.site_agents.assistant import settings as assistant_settings
from yaffo.site_agents.assistant.file_targets import SHOW_FOLDER, FileTarget, TargetError, resolve_target
from yaffo.site_agents.assistant.run_queue import run_queue_status
from yaffo.site_agents.assistant.schemas import (
    AssistantError,
    ConversationList,
    ConversationStarted,
    ConversationSummary,
    ConversationsDeleted,
    AssistantNotice,
    PlanDecided,
    conversation_status,
    plan_view,
)
from yaffo.utils.context import context
from yaffo.utils.open_in_os import open_in_os

# Pages without contextual "Ask Yaffo" buttons.
HELP_EXCLUDED_ENDPOINTS = frozenset({"settings_index"})

# Longer messages are refused rather than sent; this is a help chat, not a paste bin.
MESSAGE_MAX_LENGTH = 4000
# What a contextual "Ask Yaffo" button may attach, and how long each value may be. Anything
# else in the payload is dropped.
CONTEXT_LIMITS = {"page": 120, "job_id": 64, "automation": 120, "error_code": 64, "error": 500}


def _queue_store():
    """The task queue's store, read for a waiting run's place in line."""
    return task_queue.store


def assistant_available() -> bool:
    return not demo_mode_enabled() and assistant_settings.is_enabled()


def _error(message: str, code: str, status: int):
    return jsonify(AssistantError(error=message, code=code).to_dict()), status


def _require_available() -> None:
    if not assistant_available():
        abort(404)


def _context_from(body: dict) -> dict | None:
    """The allowlisted, length-capped context a message carries, or None."""
    raw = body.get("context")
    if not isinstance(raw, dict):
        return None
    context = {}
    for key, limit in CONTEXT_LIMITS.items():
        value = raw.get(key)
        if isinstance(value, (str, int)) and not isinstance(value, bool):
            text = " ".join(str(value).split())[:limit]
            if text:
                context[key] = text
    return context or None


def _user_payload(body: dict) -> dict | None:
    context = _context_from(body)
    return {"context": context} if context else None


def _message_or_error():
    """The request's message, or an error response to return instead."""
    message = str((request.get_json(silent=True) or {}).get("message") or "").strip()
    if not message:
        return None, _error(gettext("Message is required."), "message_required", 400)
    if len(message) > MESSAGE_MAX_LENGTH:
        return None, _error(
            gettext("Messages can be at most %(count)s characters.", count=MESSAGE_MAX_LENGTH),
            "message_too_long", 400,
        )
    if assistant_settings.api_key() is None:
        return None, _error(
            gettext(
                "No API key configured. Add your %(provider)s API key in Settings → AI Generation.",
                provider=assistant_settings.provider_label(),
            ),
            "api_key_missing", 400,
        )
    return message, None


def _get_conversation_or_404(conversation_id: int):
    conversation = repo.get_conversation(db.session, conversation_id)
    if conversation is None:
        abort(404)
    return conversation


@dataclass(frozen=True)
class SettingSwitch:
    """One checkbox in Settings → Assistant: a short label, with any explanation
    in `help` (an info tip beside it). `irreversible` marks a change that can't be
    undone (no undo, and it changes something)."""
    name: str
    label: str
    on: bool
    irreversible: bool = False
    help: str | None = None


@dataclass(frozen=True)
class ActionGroup:
    """The changes the assistant may propose that touch one kind of thing."""
    key: str
    title: str
    switches: list[SettingSwitch]

    @property
    def on_count(self) -> int:
        return sum(1 for switch in self.switches if switch.on)


def switch_help() -> dict[str, str]:
    """Info tips for the switches whose short label needs explaining."""
    return {
        assistant_settings.DIAG_METADATA: gettext(
            "Reads a photo's capture date from its file when checking a date problem, never the image itself."),
        "delete_album": gettext("The photos in the album stay in your library."),
        "delete_person": gettext("Their faces become unassigned; the photos stay in your library."),
        "ignore_faces": gettext(
            "Hides unassigned faces you don't want to name, such as strangers in the background, "
            "from Unassigned Faces. Assigned faces are never ignored."),
        "unignore_faces": gettext(
            "Puts ignored faces back in Unassigned Faces so they can be assigned to people. "
            "Faces that aren't ignored are left alone."),
        "set_person_birthdate": gettext(
            "A birthdate decides which of a person's faces are compared with each other by age, so "
            "matching for that person is rebuilt."),
        "add_label_to_vocabulary": gettext(
            "Photos only get a new label when they're classified again; the assistant can start that too."),
        "delete_label": gettext("Every photo loses that label."),
        "cancel_job": gettext(
            "Stops a pending or running job, as its Cancel button does. Work already done stays done."),
        "reindex_media": gettext("Their faces are detected again, so people assigned to them are removed."),
        "unassign_faces": gettext(
            "Takes a face off the person it's assigned to, e.g. a wrong match. The face goes back to "
            "Unassigned Faces; the photo and the person stay."),
        "merge_people": gettext(
            "Combines two people who are really the same person: every face of one moves to the other, "
            "and the emptied one is deleted."),
        "repair_face_statuses": gettext(
            "Fixes faces whose status doesn't match their person link: assigned to someone but still listed "
            "as unassigned, stuck mid-assignment, or ignored but still linked (the link is removed). "
            "Nothing is re-detected."),
        "run_automation": gettext(
            "Starts the chosen automation once for all media, selected media directories, files, or "
            "folders. The automation may change your library or files; review its Run history for the result."),
    }


def diagnostic_labels() -> dict[str, str]:
    return {
        assistant_settings.DIAG_LOGS: gettext("Logs"),
        assistant_settings.DIAG_LIBRARY: gettext("Library contents"),
        assistant_settings.DIAG_FILES: gettext("Media folders"),
        assistant_settings.DIAG_JOBS: gettext("Background jobs"),
        assistant_settings.DIAG_METADATA: gettext("Capture-date metadata"),
    }


def action_groups_layout() -> list[tuple[str, str, dict[str, str]]]:
    """(key, title, {action: label}) for each group of changes, in the order
    Settings shows them. Every assistant action belongs to exactly one group
    (tests/yaffo/routes/test_assistant_routes.py)."""
    return [
        ("tags", gettext("Tags and favorites"), {
            "tag_media_items": gettext("Add tags"),
            "untag_media_items": gettext("Remove tags"),
            "set_favorites": gettext("Mark favorites"),
        }),
        ("albums", gettext("Albums"), {
            "create_album": gettext("Create albums"),
            "update_album": gettext("Rename albums"),
            "add_to_album": gettext("Add photos to albums"),
            "remove_from_album": gettext("Remove photos from albums"),
            "set_album_cover": gettext("Change album covers"),
            "reorder_album": gettext("Reorder photos in albums"),
            "delete_album": gettext("Delete albums"),
        }),
        ("people", gettext("People and faces"), {
            "assign_faces": gettext("Assign faces to people"),
            "unassign_faces": gettext("Unassign faces"),
            "ignore_faces": gettext("Ignore faces"),
            "unignore_faces": gettext("Stop ignoring faces"),
            "set_person_birthdate": gettext("Set birthdates"),
            "create_person": gettext("Create people"),
            "rename_person": gettext("Rename people"),
            "merge_people": gettext("Merge people"),
            "delete_person": gettext("Delete people"),
            "repair_face_statuses": gettext("Repair faces"),
        }),
        ("dates", gettext("Dates and places"), {
            "set_media_dates": gettext("Change capture dates"),
            "set_location_names": gettext("Change location names"),
            "set_coordinates": gettext("Change GPS coordinates"),
        }),
        ("labels", gettext("Labels"), {
            "add_label_to_vocabulary": gettext("Add labels to the vocabulary"),
            "delete_label": gettext("Remove labels from the vocabulary"),
        }),
        ("files", gettext("Files on disk"), {
            "rename_files": gettext("Rename files"),
            "move_media_items": gettext("Move files to other folders"),
            "delete_media_items": gettext("Move photos to the system trash"),
        }),
        ("upkeep", gettext("Library upkeep"), {
            "run_automation": gettext("Run automation"),
            "reindex_media": gettext("Re-index items"),
            "cancel_job": gettext("Cancel background jobs"),
            "set_automation_enabled": gettext("Turn automations on or off"),
        }),
        ("preferences", gettext("Preferences"), {
            "set_default_theme": gettext("Change the theme"),
            "set_locale": gettext("Change the language"),
            "set_distance_unit": gettext("Change the distance unit"),
            "set_filter_layout": gettext("Arrange the sidebar filters"),
        }),
    ]


# Changes without an undo that still change nothing: no "can't be undone" tag.
_READ_ONLY_ACTIONS = frozenset()


def action_groups() -> list[ActionGroup]:
    enabled = assistant_settings.enabled_actions()
    functions = {fn.name: fn for fn in assistant_settings.action_functions()}
    help_text = switch_help()
    return [
        ActionGroup(key=key, title=title, switches=[
            SettingSwitch(
                name=name, label=label, on=name in enabled,
                irreversible=functions[name].undo is None and name not in _READ_ONLY_ACTIONS,
                help=help_text.get(name),
            )
            for name, label in labels.items() if name in functions
        ])
        for key, title, labels in action_groups_layout()
    ]


def assistant_settings_context() -> dict:
    """The Settings → Assistant section's data."""
    enabled = assistant_settings.enabled_diagnostics()
    return {
        "enabled": assistant_settings.is_enabled(),
        "diagnostics": [
            SettingSwitch(name=group, label=label, on=group in enabled, help=switch_help().get(group))
            for group, label in diagnostic_labels().items()
        ],
        "action_groups": action_groups(),
        "confirm_threshold": assistant_settings.confirm_threshold(),
    }


# PlanError codes → HTTP status. The browser words the message from the code.
PLAN_ERROR_STATUS = {
    "not_found": 404,
    "not_pending": 409,
    "expired": 409,
    "precondition": 409,
    "action_disabled": 409,
    "confirmation_required": 400,
    "not_undoable": 409,
    "not_reversible": 409,
}


def _plan_views(conversation_id: int) -> list:
    threshold = assistant_settings.confirm_threshold()
    return [plan_view(plan, threshold) for plan in repo.list_plans(db.session, conversation_id)]


def assistant_notice() -> AssistantNotice:
    return AssistantNotice(
        provider=assistant_settings.provider_label(),
        model_label=assistant_settings.model_label(),
        checks_enabled=bool(assistant_settings.enabled_diagnostics()),
    )


def _toast(response, message: str):
    response.headers["HX-Trigger"] = json.dumps({
        "showNotification": {"message": message, "type": "success"}
    })
    return response


@context("yaffo-assistant")
def init_assistant_routes(app: Flask):
    @app.context_processor
    def inject_assistant():
        available = assistant_available()
        ready = available and assistant_settings.api_key() is not None
        return {
            "assistant_available": available,
            # The floating button only shows once the assistant can actually answer.
            "assistant_ready": ready,
            # Contextual "Ask Yaffo" buttons (flashes, failure toasts, job cards, run
            # history, the error page): wherever the assistant is ready, except
            # Settings, which has no contextual help.
            "assistant_help": ready and request.endpoint not in HELP_EXCLUDED_ENDPOINTS,
            "assistant_notice": assistant_notice().to_dict() if available else None,
        }

    @app.route("/assistant", methods=["GET"])
    def assistant_index():
        _require_available()
        return render_template("assistant/index.html")

    @app.route("/api/assistant/conversations", methods=["GET"])
    def assistant_conversations():
        _require_available()
        pending = repo.pending_plan_counts(db.session)
        summaries = [ConversationSummary.from_model(c, pending.get(c.id, 0))
                     for c in repo.list_conversations(db.session)]
        return jsonify(ConversationList(conversations=summaries).to_dict())

    @app.route("/api/assistant/conversations", methods=["POST"])
    def assistant_conversation_create():
        """Start a conversation with its first message (no empty conversations)."""
        _require_available()
        message, error = _message_or_error()
        if error is not None:
            return error
        conversation = repo.create_conversation(db.session, repo.title_from_message(message))
        repo.add_event(db.session, conversation.id, ASSISTANT_EVENT_USER, message,
                       _user_payload(request.get_json(silent=True) or {}))
        repo.start_run(db.session, conversation.id, assistant_settings.resolve_model())
        assistant_run_task(conversation.id)
        conversation = repo.get_conversation(db.session, conversation.id)
        return jsonify(ConversationStarted(ConversationSummary.from_model(conversation)).to_dict()), 202

    @app.route("/api/assistant/conversations/<int:conversation_id>", methods=["GET"])
    def assistant_conversation(conversation_id: int):
        """The chat dialog's poll: status, run start time, the transcript, and
        while the run waits for a background worker, why (`queue`)."""
        _require_available()
        conversation = _get_conversation_or_404(conversation_id)
        events = repo.list_events(db.session, conversation_id)
        queue = (run_queue_status(_queue_store(), conversation_id)
                 if conversation.status == ASSISTANT_STATUS_RUNNING else None)
        return jsonify(conversation_status(conversation, events, queue, _plan_views(conversation_id)).to_dict())

    @app.route("/api/assistant/conversations/<int:conversation_id>/messages", methods=["POST"])
    def assistant_message(conversation_id: int):
        _require_available()
        conversation = _get_conversation_or_404(conversation_id)
        if conversation.status == ASSISTANT_STATUS_RUNNING:
            return _error(gettext("The assistant is still answering."), "run_in_progress", 409)
        message, error = _message_or_error()
        if error is not None:
            return error
        if not repo.start_run(db.session, conversation_id, assistant_settings.resolve_model()):
            return _error(gettext("The assistant is still answering."), "run_in_progress", 409)
        repo.add_event(db.session, conversation_id, ASSISTANT_EVENT_USER, message,
                       _user_payload(request.get_json(silent=True) or {}))
        assistant_run_task(conversation_id)
        conversation = repo.get_conversation(db.session, conversation_id)
        return jsonify(ConversationStarted(ConversationSummary.from_model(conversation)).to_dict()), 202

    def _decide_plan(conversation_id: int, plan_id: int, decide):
        """Run one plan decision. Refused while a reply is still being written: the
        run is still adding to the transcript, and the model should see the outcome
        on the next message rather than mid-answer."""
        _require_available()
        conversation = _get_conversation_or_404(conversation_id)
        if conversation.status == ASSISTANT_STATUS_RUNNING:
            return _error(gettext("Wait for the assistant to finish answering."), "run_in_progress", 409)
        try:
            plan = decide()
        except plans.PlanError as exc:
            db.session.rollback()
            return _error(str(exc), exc.code, PLAN_ERROR_STATUS.get(exc.code, 409))
        return jsonify(PlanDecided(plan_view(plan, assistant_settings.confirm_threshold())).to_dict())

    @app.route("/api/assistant/conversations/<int:conversation_id>/plans/<int:plan_id>/approve",
               methods=["POST"])
    def assistant_plan_approve(conversation_id: int, plan_id: int):
        """Approve a change plan: re-validate it, then replay its recorded calls.
        `confirm_count` is the item count the user typed or ticked, when the card
        asks for it."""
        raw = (request.get_json(silent=True) or {}).get("confirm_count")
        confirm_count = raw if isinstance(raw, int) and not isinstance(raw, bool) else None
        return _decide_plan(conversation_id, plan_id,
                            lambda: plans.approve(db.session, plan_id, conversation_id, confirm_count))

    @app.route("/api/assistant/conversations/<int:conversation_id>/plans/<int:plan_id>/decline",
               methods=["POST"])
    def assistant_plan_decline(conversation_id: int, plan_id: int):
        return _decide_plan(conversation_id, plan_id,
                            lambda: plans.decline(db.session, plan_id, conversation_id))

    @app.route("/api/assistant/conversations/<int:conversation_id>/plans/<int:plan_id>/undo",
               methods=["POST"])
    def assistant_plan_undo(conversation_id: int, plan_id: int):
        return _decide_plan(conversation_id, plan_id,
                            lambda: plans.undo(db.session, plan_id, conversation_id))

    @app.route("/api/assistant/open", methods=["POST"])
    def assistant_open():
        """An assistant "open" button (link_to_file) was clicked. The body names the
        file or folder by ids only; the path is looked up again here and must be in a
        configured media folder, so the button can't be turned into "open anything"."""
        _require_available()
        try:
            target = FileTarget.from_dict(request.get_json(silent=True))
            resolved = resolve_target(db.session, target)
        except TargetError:  # its reasons are worded for the model; the user gets one message
            return _error(gettext("That file or folder isn't available. It may have moved, or its drive "
                                  "isn't connected."), "open_target_unavailable", 404)
        try:
            open_in_os(resolved.path, reveal=target.show == SHOW_FOLDER and not resolved.is_dir)
        except (OSError, subprocess.SubprocessError):
            return _error(gettext("Couldn't open it on this computer."), "open_failed", 500)
        return "", 204

    @app.route("/api/assistant/conversations/<int:conversation_id>/cancel", methods=["POST"])
    def assistant_cancel(conversation_id: int):
        _require_available()
        _get_conversation_or_404(conversation_id)
        repo.set_status(db.session, conversation_id, ASSISTANT_STATUS_IDLE)
        return "", 204

    @app.route("/api/assistant/conversations/<int:conversation_id>", methods=["PATCH"])
    def assistant_rename(conversation_id: int):
        _require_available()
        _get_conversation_or_404(conversation_id)
        title = " ".join(str((request.get_json(silent=True) or {}).get("title") or "").split())
        if not title:
            return _error(gettext("Title is required."), "title_required", 400)
        repo.rename_conversation(db.session, conversation_id, title[:repo.TITLE_MAX_LENGTH])
        return "", 204

    @app.route("/api/assistant/conversations/<int:conversation_id>", methods=["DELETE"])
    def assistant_delete(conversation_id: int):
        """Delete a conversation. A run still working on it stops at its next step
        (its status poll finds the conversation gone)."""
        _require_available()
        if not repo.delete_conversation(db.session, conversation_id):
            abort(404)
        return "", 204

    @app.route("/settings/assistant/enabled", methods=["POST"])
    def settings_assistant_enabled():
        if demo_mode_enabled():
            abort(404)
        enabled = request.form.get("enabled") == "on"
        assistant_settings.set_enabled(enabled)
        message = gettext("Assistant turned on.") if enabled else gettext("Assistant turned off.")
        response = make_response("", 200)
        # The navbar button comes and goes with the setting; reload to apply it.
        response.headers["HX-Refresh"] = "true"
        return _toast(response, message)

    @app.route("/settings/assistant/diagnostics/<group>", methods=["POST"])
    def settings_assistant_diagnostics(group: str):
        if demo_mode_enabled() or group not in assistant_settings.DIAGNOSTIC_GROUPS:
            abort(404)
        assistant_settings.set_diagnostics_enabled(group, request.form.get("enabled") == "on")
        return _toast(make_response("", 200), gettext("Assistant settings saved."))

    @app.route("/settings/assistant/actions/<name>", methods=["POST"])
    def settings_assistant_action(name: str):
        if demo_mode_enabled() or name not in {fn.name for fn in assistant_settings.action_functions()}:
            abort(404)
        assistant_settings.set_action_enabled(name, request.form.get("enabled") == "on")
        return _toast(make_response("", 200), gettext("Assistant settings saved."))

    @app.route("/settings/assistant/confirm-threshold", methods=["POST"])
    def settings_assistant_confirm_threshold():
        if demo_mode_enabled():
            abort(404)
        try:
            count = int(request.form.get("confirm_threshold", ""))
        except ValueError:
            return _error(gettext("Enter a whole number."), "invalid_threshold", 400)
        if count < 1:
            return _error(gettext("Enter a whole number."), "invalid_threshold", 400)
        assistant_settings.set_confirm_threshold(count)
        return _toast(make_response("", 200), gettext("Assistant settings saved."))

    @app.route("/api/assistant/conversations/delete-all", methods=["POST"])
    def assistant_delete_all():
        """"Delete all conversations" under the list on the Ask Yaffo page."""
        if demo_mode_enabled():
            abort(404)
        deleted = repo.delete_all_conversations(db.session)
        return jsonify(ConversationsDeleted(deleted=deleted).to_dict())
