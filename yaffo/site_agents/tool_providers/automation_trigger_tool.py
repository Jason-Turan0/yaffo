"""Automation trigger tools: add_automation_trigger / remove_automation_trigger.

The tools the automation-builder agent calls to decide *when* its automation runs —
the companion to write_automation_code (which decides *what* it does). Scoped to a
single automation **slug** at construction, so the model can only touch that
automation's triggers.

A trigger is either a **schedule** (a 5-field cron, validated with `is_valid_cron`
before persisting — the server stays the trust boundary, same as the UI's cron
editor) or an **event** (one of the `EVENTS` catalog keys). An automation may carry
several. Both tools address a trigger by the same shape — `trigger_type` plus its
`cron` / `event_type` — so the model removes a trigger the same way it adds one (no
ids to track). Adding is idempotent (an identical trigger already present is
reported, not duplicated); removing a trigger that isn't there is reported too.
Persists via automation_repository (shared with the trigger-editing route), so the
browser sees the change after the generation finishes and the detail page reloads.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from yaffo.background_tasks.schedule import is_valid_cron
from yaffo.db.models import EVENTS, TRIGGER_TYPE_EVENT, TRIGGER_TYPE_SCHEDULE
from yaffo.db.repositories import automation_repository as repo
from yaffo.site_agents.tool_providers.tool_provider_types import (
    CallToolReturn,
    RawToolDefinition,
    ToolProvider,
)


class AutomationTriggerToolProvider(ToolProvider):
    ADD = "add_automation_trigger"
    REMOVE = "remove_automation_trigger"

    def __init__(self, slug: str, session: Session):
        self.slug = slug
        self.session = session

    def _trigger_schema(self) -> dict:
        """The shape both tools share: trigger_type + the cron / event_type that
        identifies a specific trigger."""
        event_names = ", ".join(f"{key} ({label})" for key, label in EVENTS.items())
        return {
            "type": "object",
            "properties": {
                "trigger_type": {
                    "type": "string",
                    "enum": [TRIGGER_TYPE_SCHEDULE, TRIGGER_TYPE_EVENT],
                    "description": "'schedule' (runs on a cron) or 'event' (runs on a domain event).",
                },
                "cron": {
                    "type": "string",
                    "description": (
                        "5-field cron expression (e.g. '0 3 * * *' = daily at 3am). "
                        "Required when trigger_type is 'schedule'."
                    ),
                },
                "event_type": {
                    "type": "string",
                    "enum": list(EVENTS),
                    "description": (
                        f"The domain event, one of: {event_names}. "
                        "Required when trigger_type is 'event'."
                    ),
                },
            },
            "required": ["trigger_type"],
            "additionalProperties": False,
        }

    def get_tools(self) -> list[RawToolDefinition]:
        return [
            RawToolDefinition(
                name=self.ADD,
                description=(
                    "Add a trigger that decides when this automation runs. A 'schedule' "
                    "trigger fires on a cron; an 'event' trigger fires when a domain event "
                    "occurs. An automation may have several triggers; an identical one is "
                    "not duplicated."
                ),
                input_schema=self._trigger_schema(),
            ),
            RawToolDefinition(
                name=self.REMOVE,
                description=(
                    "Remove a trigger from this automation. Identify it the same way you "
                    "add one — by trigger_type plus its cron (schedule) or event_type "
                    "(event)."
                ),
                input_schema=self._trigger_schema(),
            ),
        ]

    def call_tool(self, name: str, args: dict) -> CallToolReturn:
        if name == self.ADD:
            return self._add(args or {})
        if name == self.REMOVE:
            return self._remove(args or {})
        return f"Unknown tool: {name}"

    def _add(self, args: dict) -> CallToolReturn:
        trigger_type = (args.get("trigger_type") or "").strip()
        if trigger_type == TRIGGER_TYPE_SCHEDULE:
            return self._add_schedule(args)
        if trigger_type == TRIGGER_TYPE_EVENT:
            return self._add_event(args)
        return (
            f"Trigger not added — trigger_type must be {TRIGGER_TYPE_SCHEDULE!r} or "
            f"{TRIGGER_TYPE_EVENT!r}."
        )

    def _add_schedule(self, args: dict) -> CallToolReturn:
        cron = (args.get("cron") or "").strip()
        if not cron:
            return "Trigger not added — a schedule trigger needs a `cron` expression."
        if not is_valid_cron(cron):
            return (
                f"Trigger not added — {cron!r} is not a valid 5-field cron expression. "
                "Fix it and call add_automation_trigger again."
            )
        automation = repo.get_by_slug(self.session, self.slug)
        if automation is None:
            return f"Automation {self.slug!r} not found."
        if any(
            t.trigger_type == TRIGGER_TYPE_SCHEDULE and t.cron == cron
            for t in automation.triggers
        ):
            return f"Already has a schedule trigger for {cron!r} — nothing to add."
        repo.add_schedule_trigger(self.session, self.slug, cron)
        return f"Added a schedule trigger ({cron}) to {self.slug!r}."

    def _add_event(self, args: dict) -> CallToolReturn:
        event_type = (args.get("event_type") or "").strip()
        if event_type not in EVENTS:
            return f"Trigger not added — event_type must be one of: {', '.join(EVENTS)}."
        automation = repo.get_by_slug(self.session, self.slug)
        if automation is None:
            return f"Automation {self.slug!r} not found."
        if any(
            t.trigger_type == TRIGGER_TYPE_EVENT and t.event_type == event_type
            for t in automation.triggers
        ):
            return f"Already triggers on {event_type!r} — nothing to add."
        repo.add_event_trigger(self.session, self.slug, event_type)
        return f"Added an event trigger ({event_type}) to {self.slug!r}."

    def _remove(self, args: dict) -> CallToolReturn:
        trigger_type = (args.get("trigger_type") or "").strip()
        if trigger_type == TRIGGER_TYPE_SCHEDULE:
            return self._remove_schedule(args)
        if trigger_type == TRIGGER_TYPE_EVENT:
            return self._remove_event(args)
        return (
            f"Trigger not removed — trigger_type must be {TRIGGER_TYPE_SCHEDULE!r} or "
            f"{TRIGGER_TYPE_EVENT!r}."
        )

    def _remove_schedule(self, args: dict) -> CallToolReturn:
        cron = (args.get("cron") or "").strip()
        if not cron:
            return "Trigger not removed — name the schedule's `cron` to remove it."
        if repo.get_by_slug(self.session, self.slug) is None:
            return f"Automation {self.slug!r} not found."
        if not repo.remove_schedule_trigger(self.session, self.slug, cron):
            return f"No schedule trigger for {cron!r} to remove."
        return f"Removed the schedule trigger ({cron}) from {self.slug!r}."

    def _remove_event(self, args: dict) -> CallToolReturn:
        event_type = (args.get("event_type") or "").strip()
        if event_type not in EVENTS:
            return f"Trigger not removed — event_type must be one of: {', '.join(EVENTS)}."
        if repo.get_by_slug(self.session, self.slug) is None:
            return f"Automation {self.slug!r} not found."
        if not repo.remove_event_trigger(self.session, self.slug, event_type):
            return f"No {event_type!r} event trigger to remove."
        return f"Removed the event trigger ({event_type}) from {self.slug!r}."
