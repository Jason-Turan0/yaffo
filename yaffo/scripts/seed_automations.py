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
    EVENT_DUPLICATES_FOUND,
    EVENT_PHOTO_INDEXED,
    TRIGGER_TYPE_EVENT,
    TRIGGER_TYPE_SCHEDULE,
)

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

# On duplicates_found, move every duplicate except the keeper into a "_Duplicates"
# sub-folder of its media dir. ctx["groups"] is a list of duplicate sets, each a list
# of photo ids ordered earliest-indexed first, so group[0] is the keeper and
# group[1:] are the copies to move out.
_DEDUPE_CODE = """\
to_move = []
for group in ctx["groups"]:
    for photo_id in group[1:]:
        to_move.append(photo_id)

if to_move:
    print("Moving " + str(len(to_move)) + " duplicate(s) to _Duplicates")
    rows = data_query({"source": "photos", "id": {"in": to_move}})
    for row in rows:
        if row["media_dir_id"]:
            move_photo(row["id"], row["media_dir_id"], "_Duplicates")
"""

_EVENT_SLUG = "log-photos-on-index"
_SCHEDULE_SLUG = "log-photos-each-minute"
_ORGANIZE_SLUG = "organize-by-date"
_DEDUPE_SLUG = "move-duplicates"


def seed_automations() -> None:
    app = create_app()
    with app.app_context():
        db.create_all()

        # Replace any prior copies (ORM delete cascades to their triggers), and drop
        # any retired slugs left over from before they became system automations.
        for slug in (_EVENT_SLUG, _SCHEDULE_SLUG, _ORGANIZE_SLUG, _DEDUPE_SLUG):
            existing = db.session.query(Automation).filter_by(slug=slug).first()
            if existing is not None:
                db.session.delete(existing)
        db.session.commit()

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

        # Seeded DISABLED: it moves files, so the user opts in by enabling it. Once on,
        # any duplicate scan (the scheduled duplicate_scan automation or the manual
        # Remove Duplicates tool) fires duplicates_found and this moves the copies out.
        dedupe_automation = Automation(
            slug=_DEDUPE_SLUG,
            name="Move duplicates",
            description=(
                "When a duplicate scan finds duplicates, move every copy except the "
                "keeper into a \"_Duplicates\" sub-folder of its media dir."
            ),
            is_system=False,
            enabled=False,
            handler=None,
            published_code=_DEDUPE_CODE,
            status=AUTOMATION_STATUS_READY,
            triggers=[AutomationTrigger(
                trigger_type=TRIGGER_TYPE_EVENT,
                enabled=True,
                event_type=EVENT_DUPLICATES_FOUND,
            )],
        )

        db.session.add_all([organize_automation, dedupe_automation])
        db.session.commit()
        print(
            f"Seeded automations: '{organize_automation.slug}', '{dedupe_automation.slug}'."
        )


if __name__ == "__main__":
    seed_automations()
