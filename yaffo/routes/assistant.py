"""Routes for the in-app assistant (docs/development/ai-assistant.md, phase 1).

A turn is started here and answered by assistant_run_task in the background; the
chat UI polls the conversation until the run settles. Everything 404s when the
assistant is turned off in Settings, and in demo mode.
"""
from __future__ import annotations

import json

from flask import Flask, abort, jsonify, make_response, render_template, request
from flask_babel import gettext

from yaffo.background_tasks.tasks.assistant_run import assistant_run_task
from yaffo.db import db
from yaffo.db.models import ASSISTANT_EVENT_USER, ASSISTANT_STATUS_IDLE, ASSISTANT_STATUS_RUNNING
from yaffo.db.repositories import assistant_repository as repo
from yaffo.runtime_mode import demo_mode_enabled
from yaffo.site_agents.assistant import settings as assistant_settings
from yaffo.site_agents.assistant.schemas import (
    AssistantError,
    ConversationList,
    ConversationStarted,
    ConversationSummary,
    ConversationsDeleted,
    conversation_status,
)
from yaffo.utils.context import context

# Longer messages are refused rather than sent; this is a help chat, not a paste bin.
MESSAGE_MAX_LENGTH = 4000


def assistant_available() -> bool:
    return not demo_mode_enabled() and assistant_settings.is_enabled()


def _error(message: str, code: str, status: int):
    return jsonify(AssistantError(error=message, code=code).to_dict()), status


def _require_available() -> None:
    if not assistant_available():
        abort(404)


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


def assistant_settings_context() -> dict:
    """The Settings → Assistant section's data."""
    return {
        "enabled": assistant_settings.is_enabled(),
        "conversation_count": repo.count_conversations(db.session),
    }


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
        return {
            "assistant_available": available,
            # The floating button only shows once the assistant can actually answer.
            "assistant_ready": available and assistant_settings.api_key() is not None,
        }

    @app.route("/assistant", methods=["GET"])
    def assistant_index():
        _require_available()
        return render_template("assistant/index.html")

    @app.route("/api/assistant/conversations", methods=["GET"])
    def assistant_conversations():
        _require_available()
        summaries = [ConversationSummary.from_model(c) for c in repo.list_conversations(db.session)]
        return jsonify(ConversationList(conversations=summaries).to_dict())

    @app.route("/api/assistant/conversations", methods=["POST"])
    def assistant_conversation_create():
        """Start a conversation with its first message (no empty conversations)."""
        _require_available()
        message, error = _message_or_error()
        if error is not None:
            return error
        conversation = repo.create_conversation(db.session, repo.title_from_message(message))
        repo.add_event(db.session, conversation.id, ASSISTANT_EVENT_USER, message)
        repo.start_run(db.session, conversation.id, assistant_settings.resolve_model())
        assistant_run_task(conversation.id)
        conversation = repo.get_conversation(db.session, conversation.id)
        return jsonify(ConversationStarted(ConversationSummary.from_model(conversation)).to_dict()), 202

    @app.route("/api/assistant/conversations/<int:conversation_id>", methods=["GET"])
    def assistant_conversation(conversation_id: int):
        """The chat dialog's poll: status, run start time, and the transcript."""
        _require_available()
        conversation = _get_conversation_or_404(conversation_id)
        events = repo.list_events(db.session, conversation_id)
        return jsonify(conversation_status(conversation, events).to_dict())

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
        repo.add_event(db.session, conversation_id, ASSISTANT_EVENT_USER, message)
        assistant_run_task(conversation_id)
        conversation = repo.get_conversation(db.session, conversation_id)
        return jsonify(ConversationStarted(ConversationSummary.from_model(conversation)).to_dict()), 202

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

    @app.route("/api/assistant/conversations/delete-all", methods=["POST"])
    def assistant_delete_all():
        """Settings → Assistant → Delete all conversations. Works while the
        assistant is turned off, so history can still be cleared."""
        if demo_mode_enabled():
            abort(404)
        deleted = repo.delete_all_conversations(db.session)
        return jsonify(ConversationsDeleted(deleted=deleted).to_dict())
