"""Change plans: record, approve, replay, undo (docs/development/ai-assistant.md →
Change plans).

A run_script run binds the assistant profile's mutating host functions in
recording mode, so a script's changes come back as calls, not effects. This module
freezes those calls into an `AssistantChangePlan` the user approves on a card.

- **Record.** Every call must be an allowlisted mutation whose Settings switch is
  on. Preconditions that don't depend on an earlier step are checked now. Each
  step keeps the server's own summary and facts (counts, tag and album names) for
  the card; nothing the model wrote is shown as the change.
- **Approve.** Re-check expiry, switches, preconditions and the count
  confirmation, claim the plan in one UPDATE, then **replay** the frozen calls in
  order through the live host functions. Reference tokens ($ref:N) resolve to
  earlier steps' results. Just before a step runs, its `undo` captures the
  reversing calls. The script is never re-run.
- **Undo.** Replays each finished step's reversing calls, last step first. The
  inverses carry expected values, so an item changed since is left alone.

Every decision appends a `plan` event to the transcript: the chat's card follows
the plan row, and the model reads the outcome on the user's next message.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from typing import Any, Optional

from sqlalchemy.orm import Session

from yaffo.background_tasks.automation_sandbox.automation_host import (
    build_host_functions,
    host_function,
    summarize_call,
)
from yaffo.background_tasks.automation_sandbox import maintenance_actions as maintenance
from yaffo.background_tasks.automation_sandbox.host_types import HostCall, resolve_references
from yaffo.background_tasks.automation_sandbox.undo import resolve_undo_result
from yaffo.db.models import (
    ASSISTANT_EVENT_PLAN,
    PLAN_STATUS_APPROVED,
    PLAN_STATUS_DECLINED,
    PLAN_STATUS_EXECUTED,
    PLAN_STATUS_EXPIRED,
    PLAN_STATUS_FAILED,
    PLAN_STATUS_PARTIAL,
    PLAN_STATUS_PENDING,
    PLAN_STATUS_UNDONE,
    Album,
    AssistantChangePlan,
    Person,
    PersonFace,
)
from yaffo.db.repositories import assistant_repository as repo
from yaffo.db.repositories import automation_repository
from yaffo.logging_config import get_logger
from yaffo.site_agents.assistant import settings as assistant_settings
from yaffo.site_agents.assistant.app_pages import page_url
from yaffo.utils.time import utcnow

logger = get_logger(__name__, "background_tasks")

PROFILE = "assistant"
# A pending card is re-validated at approval; after this it can't be approved.
PLAN_TTL = timedelta(minutes=30)
MAX_PLAN_STEPS = 50
MAX_ERROR_CHARS = 500
# Names listed on a card per step ("tags 'a', 'b', 'c' and 2 more").
MAX_FACT_NAMES = 3

RISK_ORDER = {"low": 0, "medium": 1, "high": 2}

# Step states, set by replay and undo.
STEP_PENDING = "pending"
STEP_DONE = "done"
STEP_FAILED = "failed"
STEP_NOT_RUN = "not_run"
STEP_UNDONE = "undone"

# How approval must be confirmed on the card.
CONFIRM_TYPE = "type"     # high risk: type the item count
CONFIRM_CHECK = "check"   # above the count threshold: tick "Yes, change all N"

# Plans in these states ran at least partly and can be undone.
UNDOABLE = (PLAN_STATUS_EXECUTED, PLAN_STATUS_PARTIAL)


class PlanError(Exception):
    """A plan can't be recorded or acted on. `code` is for the browser (localized
    there); the message is for the model and the logs."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass
class PlanStep:
    seq: int
    name: str
    args: list[Any]
    summary: str
    facts: dict[str, Any]
    count: int
    risk: str
    reversible: bool
    state: str = STEP_PENDING
    result: Any = None
    undo: list[dict] = field(default_factory=list)
    error: Optional[str] = None
    # Background work: the step's result is a Job id, and `job_page` the app page
    # (app-relative URL) where that job shows, if any.
    starts_job: bool = False
    job_page: Optional[str] = None

    @property
    def undoable(self) -> bool:
        """Reversible, or changes nothing (a scan) so there's nothing to reverse."""
        return self.reversible or bool(self.facts.get("read_only"))

    @classmethod
    def from_dict(cls, data: dict) -> "PlanStep":
        return cls(**data)

    def to_dict(self) -> dict:
        return asdict(self)


# ---- recording ------------------------------------------------------------------

def _has_reference(value: Any) -> bool:
    if isinstance(value, str):
        return value.startswith("$ref:")
    if isinstance(value, list):
        return any(_has_reference(item) for item in value)
    if isinstance(value, dict):
        return any(_has_reference(item) for item in value.values())
    return False


def _names(values: list[str]) -> list[str]:
    return list(dict.fromkeys(v for v in values if v))


def _created_name(value: Any, mutations: list[HostCall], creator: str) -> Optional[str]:
    """The name a `creator` step earlier in the plan was called with, for a $ref:N
    that points at it."""
    targets = {i: c for i, c in enumerate(mutations) if c.name == creator}
    try:
        return str(resolve_references(value, targets).args[0]).strip()
    except (ValueError, IndexError, AttributeError):
        return None


def _album_name(session: Session, value: Any, mutations: list[HostCall]) -> Optional[str]:
    """An album argument as a name: an existing album's, or the one a create_album
    step earlier in the plan will make."""
    if isinstance(value, str) and value.startswith("$ref:"):
        return _created_name(value, mutations, "create_album")
    album = session.get(Album, value) if isinstance(value, int) else None
    return album.name if album else None


def _person_name(session: Session, value: Any, mutations: list[HostCall]) -> Optional[str]:
    """A person argument as a name: an existing person's, or the one a create_person
    step earlier in the plan will make."""
    if isinstance(value, str) and value.startswith("$ref:"):
        return _created_name(value, mutations, "create_person")
    person = session.get(Person, value) if isinstance(value, int) else None
    return person.name if person else None


def _list_arg(args: list[Any], index: int) -> list:
    return args[index] if len(args) > index and isinstance(args[index], list) else []


def step_facts(call: HostCall, session: Session, mutations: list[HostCall]) -> tuple[int, dict[str, Any]]:
    """What a step affects, read from its frozen arguments: the item count and the
    names the card shows (tags, album, people, a uniform new value). The browser
    words the card from these in the user's language."""
    args, name = call.args, call.name
    facts: dict[str, Any] = {}
    if name in {"tag_media_items", "untag_media_items"}:
        entries = [e for e in _list_arg(args, 0) if isinstance(e, dict)]
        labels = _names([f"{e.get('name')}={e['value']}" if e.get("value") else str(e.get("name") or "")
                         for e in entries])
        facts["names"] = labels[:MAX_FACT_NAMES]
        facts["more"] = max(0, len(labels) - MAX_FACT_NAMES)
        return len({e.get("media_item_id") for e in entries}), facts
    if name in {"assign_faces", "unassign_faces"}:
        entries = [e for e in _list_arg(args, 0) if isinstance(e, dict)]
        person_ids = list(dict.fromkeys(e["person_id"] for e in entries if e.get("person_id") is not None))
        people = _names([_person_name(session, pid, mutations) or "" for pid in person_ids])
        facts["names"] = people[:MAX_FACT_NAMES]
        facts["more"] = max(0, len(people) - MAX_FACT_NAMES)
        return len({e.get("face_id") for e in entries}), facts
    if name == "create_album":
        facts["album"] = str(args[0]).strip() if args else ""
        return 1, facts
    if name == "update_album":
        facts["album"] = _album_name(session, args[0] if args else None, mutations)
        facts["name"] = str(args[1]).strip() if len(args) > 1 else ""
        return 1, facts
    if name in {"add_to_album", "remove_from_album"}:
        facts["album"] = _album_name(session, args[0] if args else None, mutations)
        facts["new_album"] = bool(args) and _has_reference(args[0])
        return len(set(_list_arg(args, 1))), facts
    if name == "delete_album":
        facts["album"] = _album_name(session, args[0] if args else None, mutations)
        return 1, facts
    if name in {"set_favorites", "set_media_dates", "set_location_names"}:
        field_name = {"set_favorites": "favorite", "set_media_dates": "date",
                      "set_location_names": "location_name"}[name]
        entries = [e for e in _list_arg(args, 0) if isinstance(e, dict)]
        values = {json.dumps(e.get(field_name)) for e in entries}
        if len(values) == 1 and entries:
            facts["value"] = entries[0].get(field_name)
        return len({e.get("id") for e in entries}), facts
    if name == "create_person":
        facts["person"] = str(args[0]).strip() if args else ""
        return 1, facts
    if name == "rename_person":
        facts["person"] = _person_name(session, args[0] if args else None, mutations)
        facts["name"] = str(args[1]).strip() if len(args) > 1 else ""
        return 1, facts
    if name in {"merge_people", "delete_person"}:
        # Counted in faces, the thing the change moves or unassigns (at least 1, so
        # a typed confirmation never asks for 0).
        person_id = args[0] if args else None
        facts["person"] = _person_name(session, person_id, mutations)
        if name == "merge_people":
            facts["target"] = _person_name(session, args[1] if len(args) > 1 else None, mutations)
        faces = session.query(PersonFace).filter(PersonFace.person_id == person_id).count() \
            if isinstance(person_id, int) else 0
        facts["faces"] = faces
        return max(faces, 1), facts
    if name == "set_automation_enabled":
        automation = automation_repository.get_by_slug(session, args[0]) if args else None
        facts["automation"] = automation.display_name if automation else None
        facts["value"] = bool(args[1]) if len(args) > 1 else None
        return 1, facts
    if name == "run_automation":
        automation = automation_repository.get_by_slug(session, args[0]) if args else None
        facts["automation"] = automation.display_name if automation else None
        _, scope = maintenance.resolve_run_scope(session, automation.handler if automation else None,
                                                 args[1] if len(args) > 1 else None)
        facts["scope"] = scope
        return 1, facts
    if name == "reindex_media":
        return len(set(_list_arg(args, 0))), facts
    if name == "repair_face_statuses":
        problems = maintenance.face_repair_counts(session)
        facts.update(linked=problems.linked_not_assigned,
                     processing=problems.processing_linked + problems.processing_unlinked,
                     ignored=problems.ignored_linked)
        return problems.total, facts
    if name in {"rename_files", "move_media_items"}:
        return len({e.get("media_item_id") for e in _list_arg(args, 0) if isinstance(e, dict)}), facts
    if name == "delete_media_items":
        return len(set(_list_arg(args, 0))), facts
    return 0, facts


def record_plan(
    session: Session, conversation_id: int, calls: list[HostCall], script: str,
    enabled_actions: frozenset[str],
) -> Optional[AssistantChangePlan]:
    """Freeze a preview run's mutating calls as a PENDING plan, or None when the
    script only read. Raises PlanError when a call isn't allowed or already can't
    apply; nothing is saved then."""
    mutations = [c for c in calls if host_function(c.name, PROFILE).mutating]
    if not mutations:
        return None
    if len(mutations) > MAX_PLAN_STEPS:
        raise PlanError("too_many_steps",
                        f"A plan can have at most {MAX_PLAN_STEPS} steps; batch items into fewer calls.")
    steps: list[PlanStep] = []
    for seq, call in enumerate(mutations):
        fn = host_function(call.name, PROFILE)
        if call.name not in enabled_actions:
            raise PlanError("action_disabled", f"{call.name} is turned off in Settings → Assistant.")
        if fn.precondition is not None and not _has_reference(call.args):
            reason = fn.precondition(call.args, session)
            if reason:
                raise PlanError("precondition", f"Step {seq + 1} ({call.name}) can't apply: {reason}")
        count, facts = step_facts(call, session, mutations)
        steps.append(PlanStep(
            seq=seq, name=call.name, args=call.args,
            summary=summarize_call(call, session, mutations),
            facts=facts, count=count, risk=fn.risk,
            reversible=fn.undo is not None,
            starts_job=fn.starts_job,
            job_page=(page_url("automations_show", {"slug": call.args[0]}) if call.name == "run_automation"
                      else page_url(fn.job_page, {}) if fn.job_page else None),
        ))
    risk = max((s.risk for s in steps), key=RISK_ORDER.__getitem__)
    return repo.create_plan(
        session, conversation_id, script=script, steps=[s.to_dict() for s in steps],
        risk=risk, ttl=PLAN_TTL,
    )


def describe_for_model(plan: AssistantChangePlan) -> str:
    """The run_script result when a plan was recorded."""
    steps = load_steps(plan)
    lines = [f"Recorded change plan #{plan.id} ({len(steps)} step{'s' if len(steps) != 1 else ''}, "
             f"{plan.risk} risk):"]
    lines += [f"{s.seq + 1}. {s.summary}" for s in steps]
    lines.append("Nothing has changed yet. The user sees this plan as a card under your reply and "
                 "approves or declines it there. Don't say it has run; tell them to review the card.")
    return "\n".join(lines)


# ---- reading --------------------------------------------------------------------

def load_steps(plan: AssistantChangePlan) -> list[PlanStep]:
    return [PlanStep.from_dict(step) for step in json.loads(plan.steps_json)]


def plan_count(steps: list[PlanStep]) -> int:
    """The number the card confirms: the largest set any step touches."""
    return max((s.count for s in steps), default=0)


def is_expired(plan: AssistantChangePlan, now: Optional[datetime] = None) -> bool:
    return plan.status == PLAN_STATUS_PENDING and (now or utcnow()) >= plan.expires_at


def effective_status(plan: AssistantChangePlan, now: Optional[datetime] = None) -> str:
    """A pending plan past its time reads as EXPIRED even before anyone acts on it."""
    return PLAN_STATUS_EXPIRED if is_expired(plan, now) else plan.status


def confirmation(plan: AssistantChangePlan, steps: list[PlanStep], threshold: int) -> Optional[str]:
    if plan.risk == "high":
        return CONFIRM_TYPE
    if plan_count(steps) > threshold:
        return CONFIRM_CHECK
    return None


# ---- deciding ---------------------------------------------------------------------

def _plan_event(session: Session, plan: AssistantChangePlan, text: str) -> None:
    repo.add_event(session, plan.conversation_id, ASSISTANT_EVENT_PLAN, text,
                   {"plan_id": plan.id, "status": plan.status})


def _require(session: Session, plan_id: int, conversation_id: int) -> AssistantChangePlan:
    plan = repo.get_plan(session, plan_id)
    if plan is None or plan.conversation_id != conversation_id:
        raise PlanError("not_found", "No such plan.")
    return plan


def _expire(session: Session, plan: AssistantChangePlan, reason: str) -> None:
    if repo.claim_plan(session, plan.id, (PLAN_STATUS_PENDING,), PLAN_STATUS_EXPIRED,
                       decided_at=utcnow(), error=reason[:MAX_ERROR_CHARS]):
        session.refresh(plan)
        _plan_event(session, plan, f"Plan #{plan.id} expired before it ran: {reason}")


def decline(session: Session, plan_id: int, conversation_id: int) -> AssistantChangePlan:
    plan = _require(session, plan_id, conversation_id)
    if is_expired(plan):
        _expire(session, plan, "not approved in time")
        raise PlanError("expired", "The plan expired.")
    if not repo.claim_plan(session, plan.id, (PLAN_STATUS_PENDING,), PLAN_STATUS_DECLINED, decided_at=utcnow()):
        raise PlanError("not_pending", "The plan was already decided.")
    session.refresh(plan)
    _plan_event(session, plan, f"The user declined plan #{plan.id}. Nothing was changed.")
    return plan


def approve(session: Session, plan_id: int, conversation_id: int,
            confirm_count: Optional[int] = None) -> AssistantChangePlan:
    """Re-validate, claim, and replay a pending plan. Raises PlanError when it
    can't run; a plan whose preconditions no longer hold expires."""
    plan = _require(session, plan_id, conversation_id)
    if plan.status != PLAN_STATUS_PENDING:
        raise PlanError("not_pending", "The plan was already decided.")
    if is_expired(plan):
        _expire(session, plan, "not approved in time")
        raise PlanError("expired", "The plan expired.")
    steps = load_steps(plan)
    enabled = assistant_settings.enabled_actions(session)
    disabled = [s.name for s in steps if s.name not in enabled]
    if disabled:
        raise PlanError("action_disabled", f"Turned off in Settings: {', '.join(disabled)}")
    for step in steps:
        fn = host_function(step.name, PROFILE)
        if fn.precondition is not None and not _has_reference(step.args):
            reason = fn.precondition(step.args, session)
            if reason:
                _expire(session, plan, f"step {step.seq + 1} can no longer apply: {reason}")
                raise PlanError("precondition", reason)
    if confirmation(plan, steps, assistant_settings.confirm_threshold(session)) and confirm_count != plan_count(steps):
        raise PlanError("confirmation_required", "Confirm the number of items first.")
    if not repo.claim_plan(session, plan.id, (PLAN_STATUS_PENDING,), PLAN_STATUS_APPROVED, decided_at=utcnow()):
        raise PlanError("not_pending", "The plan was already decided.")
    session.refresh(plan)
    _replay(session, plan, steps)
    return plan


def _replay(session: Session, plan: AssistantChangePlan, steps: list[PlanStep]) -> None:
    functions = build_host_functions(session, profile=PROFILE)
    results: dict[int, Any] = {}
    failed: Optional[PlanStep] = None
    for step in steps:
        if failed is not None:
            step.state = STEP_NOT_RUN
            continue
        fn = host_function(step.name, PROFILE)
        try:
            args = resolve_references(step.args, results)
            if fn.precondition is not None:
                reason = fn.precondition(args, session)
                if reason:
                    raise PlanError("precondition", reason)
            inverse = fn.undo(args, session) if fn.undo is not None else None
            result = functions[step.name](*args)
        except Exception as exc:  # the step's own validation or its write failed
            session.rollback()
            logger.warning(f"assistant plan {plan.id}: step {step.seq + 1} ({step.name}) failed: {exc}")
            step.state, step.error = STEP_FAILED, str(exc)[:MAX_ERROR_CHARS]
            failed = step
            continue
        if fn.returns_value:
            results[step.seq] = result
        step.state, step.result = STEP_DONE, result
        step.undo = [asdict(call) for call in resolve_undo_result(inverse or [], result)]

    done = sum(1 for s in steps if s.state == STEP_DONE)
    if failed is None:
        status, text = PLAN_STATUS_EXECUTED, f"The user approved plan #{plan.id}; all {done} step(s) ran."
    else:
        status = PLAN_STATUS_PARTIAL if done else PLAN_STATUS_FAILED
        text = (f"The user approved plan #{plan.id}. {done} of {len(steps)} step(s) ran; "
                f"step {failed.seq + 1} failed: {failed.error}")
    jobs = [s for s in steps if s.starts_job and s.state == STEP_DONE]
    if jobs:
        text += " " + " ".join(f"Step {s.seq + 1} started background job {s.result}." for s in jobs)
        text += " That work may still be running: check it with job_detail before saying how it went."
    runs = [s for s in steps if s.name == "run_automation" and s.state == STEP_DONE]
    if runs:
        text += " " + " ".join(
            f"Step {s.seq + 1} queued automation {s.args[0]!r}." for s in runs
        )
        text += " Its Job appears in that automation's Run history when the worker starts; check there before saying how it went."
    repo.save_plan(session, plan, steps=[s.to_dict() for s in steps], status=status,
                   finished_at=utcnow(), error=failed.error if failed else None)
    _plan_event(session, plan, text)


def undo(session: Session, plan_id: int, conversation_id: int) -> AssistantChangePlan:
    """Reverse the steps that ran, last first. Items changed since are left as they
    are (the inverses compare before writing)."""
    plan = _require(session, plan_id, conversation_id)
    steps = load_steps(plan)
    if plan.status not in UNDOABLE:
        raise PlanError("not_undoable", "Only a plan that ran can be undone.")
    ran = [s for s in steps if s.state == STEP_DONE]
    if not all(s.undoable for s in ran):
        raise PlanError("not_reversible", "This plan can't be undone.")
    if all(s.facts.get("read_only") for s in ran):
        raise PlanError("not_undoable", "This plan changed nothing, so there's nothing to undo.")
    if not repo.claim_plan(session, plan.id, UNDOABLE, PLAN_STATUS_UNDONE):
        raise PlanError("not_undoable", "The plan was already undone.")
    session.refresh(plan)
    functions = build_host_functions(session, profile=PROFILE)
    errors: list[str] = []
    for step in reversed(steps):
        if step.state != STEP_DONE:
            continue
        try:
            for call in step.undo:
                host_function(call["name"], PROFILE)
                functions[call["name"]](*call["args"])
            step.state = STEP_UNDONE
        except Exception as exc:
            session.rollback()
            logger.warning(f"assistant plan {plan.id}: undo of step {step.seq + 1} failed: {exc}")
            errors.append(f"step {step.seq + 1}: {exc}"[:MAX_ERROR_CHARS])
    error = "; ".join(errors)[:MAX_ERROR_CHARS] or None
    repo.save_plan(session, plan, steps=[s.to_dict() for s in steps], finished_at=utcnow(), error=error)
    text = f"The user undid plan #{plan.id}." + (f" Some steps couldn't be undone: {error}" if error else "")
    _plan_event(session, plan, text)
    return plan
