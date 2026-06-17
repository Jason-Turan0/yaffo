#!/usr/bin/env python3
"""Seed example *custom* automations (Starlark-backed).

Stand-ins for what the agent would generate, so the automation runtime has
something to exercise end-to-end without the builder UI:

1. "Log photos on index" — an EVENT automation subscribed to `photo_indexed`
   (fires when an index/import job completes),
2. "Log photos each minute" — a SCHEDULE automation on `* * * * *`, and
3. "Organize by date" — an EVENT automation that moves each indexed photo into a
   Year/Month sub-folder of its media dir (using data_query's media_dir_id +
   move_photo), demonstrating the read + mutating host API.

(Auto-assign faces was promoted to a built-in *system* automation —
background_tasks.tasks.auto_assign_faces_automation, seeded by init_db — so it's
no longer a Starlark seed here. The old custom slug is cleaned up below.)

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

# On photo_indexed, move each photo into a Year/Month sub-folder of its media dir.
# data_query gives each photo's media_dir_id + year/month; move_photo keeps the
# file name and is confined to that media dir.
_ORGANIZE_CODE = """\
rows = data_query({"source": "photos", "id": {"in": ctx["photo_ids"]}})
for row in rows:
    if row["year"] and row["month"] and row["media_dir_id"]:
        month = row["month"]
        mm = str(month) if month >= 10 else "0" + str(month)
        move_photo(row["id"], row["media_dir_id"], str(row["year"]) + "/" + mm)
"""

_EVENT_SLUG = "log-photos-on-index"
_SCHEDULE_SLUG = "log-photos-each-minute"
_ORGANIZE_SLUG = "organize-by-date"

# Promoted to a system automation; deleted here so re-running the seeder removes any
# stale custom copy from before the promotion.
_RETIRED_SLUGS = ("auto-assign-faces",)


def seed_automations() -> None:
    app = create_app()
    with app.app_context():
        db.create_all()

        # Replace any prior copies (ORM delete cascades to their triggers), and drop
        # any retired slugs left over from before they became system automations.
        for slug in (_EVENT_SLUG, _SCHEDULE_SLUG, _ORGANIZE_SLUG, *_RETIRED_SLUGS):
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

        organize_automation = Automation(
            slug=_ORGANIZE_SLUG,
            name="Organize by date",
            description=(
                "When a photo is indexed, move it into a Year/Month sub-folder of its "
                "media dir (keeping its file name)."
            ),
            is_system=False,
            enabled=True,
            handler=None,
            published_code=_ORGANIZE_CODE,
            status=AUTOMATION_STATUS_READY,
            triggers=[AutomationTrigger(
                trigger_type=TRIGGER_TYPE_EVENT,
                enabled=True,
                event_type=EVENT_PHOTO_INDEXED,
            )],
        )

        db.session.add_all([event_automation, schedule_automation, organize_automation])
        db.session.commit()
        print(
            f"Seeded automations: '{event_automation.slug}', '{schedule_automation.slug}', "
            f"and '{organize_automation.slug}'."
        )


if __name__ == "__main__":
    seed_automations()
