#!/usr/bin/env python3
"""Seed example *custom* automations (Starlark-backed).

Stand-ins for what the agent would generate, so the automation runtime has
something to exercise end-to-end without the builder UI:

1. "Log photos on index" — an EVENT automation subscribed to `photo_indexed`
   (fires when an index/import job completes),
2. "Log photos each minute" — a SCHEDULE automation on `* * * * *`, and
3. "Auto-assign faces" — an EVENT automation that, on `photo_indexed`, assigns each
   face to the one person it matches at 95%+ (using match_people + assign_face),
   demonstrating the read + mutating host API.

All are custom (handler=None, code set) and enabled. The first two run a
data_query and print rows; the sandbox captures the prints and the executor logs
them, so the rows show up in the consumer console.

Needs the Huey consumer running to actually fire (`inv start-tasks`). Run:
    python -m yaffo.scripts.seed_automations

Idempotent: re-running replaces the two seeded automations (and their triggers).
"""
from __future__ import annotations

from yaffo.app import create_app
from yaffo.db import db
from yaffo.db.models import (
    Automation,
    AutomationTrigger,
    AUTOMATION_STATUS_READY,
    EVENT_PHOTO_INDEXED,
    TRIGGER_TYPE_EVENT,
    TRIGGER_TYPE_SCHEDULE,
)

# Each script reads `ctx` (the trigger context) and calls the host API
# (data_query) bound to the run's session; print() output is captured by the
# sandbox and logged by the executor. See docs/automations.md.

_EVENT_CODE = """\
ids = ctx["photo_ids"]
print("photo_indexed event:", ctx["job_id"], "indexed", len(ids), "photos")
if ids:
    rows = data_query({"source": "photos", "id": {"in": ids}})
    for row in rows:
        print(row)
"""

_SCHEDULE_CODE = """\
rows = data_query({"source": "photos", "limit": 10})
print("scheduled run — first", len(rows), "photos:")
for row in rows:
    print(row)
"""

# On photo_indexed, assign each face to the one person it matches at 95%+, leaving
# a face unassigned when it matches several people. The threshold/ambiguity logic
# lives in the script; match_people does the per-face scoring
# (calculate_face_similarity) and assign_face performs the action per face only when
# exactly one person clears the bar.
_ASSIGN_FACES_CODE = """\
THRESHOLD = 0.95
for photo_id in ctx["photo_ids"]:
    for face in match_people(photo_id):
        strong = [m["person_id"] for m in face["matches"] if m["score"] >= THRESHOLD]
        if len(strong) == 1:
            assign_face(face["face_id"], strong[0])
"""

_EVENT_SLUG = "log-photos-on-index"
_SCHEDULE_SLUG = "log-photos-each-minute"
_ASSIGN_FACES_SLUG = "auto-assign-faces"


def seed_automations() -> None:
    app = create_app()
    with app.app_context():
        db.create_all()

        # Replace any prior copies (ORM delete cascades to their triggers).
        for slug in (_EVENT_SLUG, _SCHEDULE_SLUG, _ASSIGN_FACES_SLUG):
            existing = db.session.query(Automation).filter_by(slug=slug).first()
            if existing is not None:
                db.session.delete(existing)
        db.session.commit()

        event_automation = Automation(
            slug=_EVENT_SLUG,
            name="Log photos on index",
            description="On photo_indexed, log the first 10 photos.",
            is_system=False,
            enabled=True,
            handler=None,
            published_code=_EVENT_CODE,
            status=AUTOMATION_STATUS_READY,
            triggers=[AutomationTrigger(
                trigger_type=TRIGGER_TYPE_EVENT,
                enabled=True,
                event_type=EVENT_PHOTO_INDEXED,
            )],
        )

        schedule_automation = Automation(
            slug=_SCHEDULE_SLUG,
            name="Log photos each minute",
            description="Every minute, log the first 10 photos.",
            is_system=False,
            enabled=True,
            handler=None,
            published_code=_SCHEDULE_CODE,
            status=AUTOMATION_STATUS_READY,
            triggers=[AutomationTrigger(
                trigger_type=TRIGGER_TYPE_SCHEDULE,
                enabled=True,
                cron="* * * * *",
            )],
        )

        assign_faces_automation = Automation(
            slug=_ASSIGN_FACES_SLUG,
            name="Auto-assign faces",
            description=(
                "When a photo is indexed, assign each face to the one person it "
                "matches at 95%+ — a face matching several people is left unassigned."
            ),
            is_system=False,
            enabled=True,
            handler=None,
            published_code=_ASSIGN_FACES_CODE,
            status=AUTOMATION_STATUS_READY,
            triggers=[AutomationTrigger(
                trigger_type=TRIGGER_TYPE_EVENT,
                enabled=True,
                event_type=EVENT_PHOTO_INDEXED,
            )],
        )

        db.session.add_all([event_automation, schedule_automation, assign_faces_automation])
        db.session.commit()
        print(
            f"Seeded automations: '{event_automation.slug}' (event: {EVENT_PHOTO_INDEXED}), "
            f"'{schedule_automation.slug}' (schedule: * * * * *), "
            f"and '{assign_faces_automation.slug}' (event: {EVENT_PHOTO_INDEXED})."
        )


if __name__ == "__main__":
    seed_automations()
