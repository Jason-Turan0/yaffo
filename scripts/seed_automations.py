#!/usr/bin/env python3
"""Seed example *custom* automations (Starlark-backed).

Stand-ins for what the agent would generate, so the automation runtime has
something to exercise end-to-end without the builder UI:

1. "Log photos on index" — an EVENT automation subscribed to `photo_indexed`
   (fires when an index/import job completes),
2. "Log photos each minute" — a SCHEDULE automation on `* * * * *`, and
3. "Organize by date" — an EVENT automation that moves each indexed photo into a
   Year/Month sub-folder of its media dir (using data_query's media_dir_id +
   move_media_item), demonstrating the read + mutating host API.

All are custom (handler=None, code set) and enabled. The first two run a
data_query and print rows; the sandbox captures the prints and the executor logs
them, so the rows show up in the consumer console.

Needs the task host running to actually fire (`inv start-tasks`). Run:
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
    EVENT_MEDIA_INDEXED,
    TRIGGER_TYPE_EVENT,
    TRIGGER_TYPE_SCHEDULE,
)

# On photo_indexed, move each photo into a Year/Month sub-folder of its media dir.
# data_query gives each photo's media_dir_id + year/month; the moves are collected
# and written in one batched move_media_items call (see <batching> in the prompt).
_ORGANIZE_CODE = """\
rows = data_query({"source": "media_items", "id": {"in": ctx["media_item_ids"]}})
moves = []
for row in rows:
    if row["year"] and row["month"] and row["media_dir_id"]:
        month = row["month"]
        mm = str(month) if month >= 10 else "0" + str(month)
        moves.append({"media_item_id": row["id"], "media_dir_id": row["media_dir_id"], "target_path": str(row["year"]) + "/" + mm})
move_media_items(moves)
"""



# A library-wide organize for the prompt "Take all my favorite photos of my kids
# Maya and Theo and put them in their own folders grouped by year". Context-less
# (queries the whole library, not ctx), so it's run via Run now. Walks people ->
# their faces -> the photos those faces are in, keeps the favorites, and batch-moves
# each into "<Kid>/<Year>" within its media dir. Exercises the favorite filter,
# report_progress, and the batched move_media_items write.
_FILE_KIDS_CODE = """\
people = data_query({"source": "people", "name": {"in": ["Maya Bennett", "Theo Bennett"]}})
name_by_person = {p["id"]: p["name"] for p in people}
person_ids = [p["id"] for p in people]

moves = []
if person_ids:
    links = data_query({"source": "people_face", "person_id": {"in": person_ids}})
    person_by_face = {link["face_id"]: link["person_id"] for link in links}
    face_ids = [link["face_id"] for link in links]

    person_by_photo = {}
    if face_ids:
        faces = data_query({"source": "faces", "id": {"in": face_ids}})
        for face in faces:
            media_item_id = face["media_item_id"]
            if media_item_id != None and media_item_id not in person_by_photo:
                person_by_photo[media_item_id] = person_by_face[face["id"]]

    media_item_ids = [pid for pid in person_by_photo]
    if media_item_ids:
        favorites = data_query({"source": "media_items", "favorite": {"eq": True}, "id": {"in": media_item_ids}})
        total = len(favorites)
        for i, row in enumerate(favorites):
            if row["year"] and row["media_dir_id"]:
                kid = name_by_person[person_by_photo[row["id"]]]
                moves.append({
                    "media_item_id": row["id"],
                    "media_dir_id": row["media_dir_id"],
                    "target_path": kid + "/" + str(row["year"]),
                })
            report_progress(i + 1, total)

print("Filing " + str(len(moves)) + " favorite photo(s) of Maya Bennett & Theo Bennett into <kid>/<year> folders")
move_media_items(moves)
"""

# Always raises, to exercise the error path (a FAILED run in the history).
_ERROR_CODE = 'fail("This automation always fails — used to test error handling.")\n'

_EVENT_SLUG = "log-photos-on-index"
_SCHEDULE_SLUG = "log-photos-each-minute"
_ORGANIZE_SLUG = "organize-by-date"
_DEDUPE_SLUG = "move-duplicates"
_FILE_KIDS_SLUG = "file-favorite-kid-photos"
_ERROR_SLUG = "always-fails"


def seed_automations() -> None:
    app = create_app()
    with app.app_context():
        db.create_all()

        # Replace any prior copies (ORM delete cascades to their triggers), and drop
        # any retired slugs left over from before they became system automations.
        for slug in (_EVENT_SLUG, _SCHEDULE_SLUG, _ORGANIZE_SLUG, _DEDUPE_SLUG, _FILE_KIDS_SLUG, _ERROR_SLUG):
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
                event_type=EVENT_MEDIA_INDEXED,
            )],
        )

        # A library-wide organize, the kind the builder would generate from a natural
        # prompt. No triggers — it queries the whole library, not an event's photos —
        # so it's invoked via Run now on the detail page. Seeded DISABLED since it moves
        # files; the user enables it and clicks Run now.
        file_kids_automation = Automation(
            slug=_FILE_KIDS_SLUG,
            name="File favorite kid photos",
            description=(
                "Take all favorite photos of Chase and Nathan and move each into a "
                "\"<kid>/<year>\" folder within its media dir. Run it from Run now."
            ),
            is_system=False,
            enabled=False,
            handler=None,
            published_code=_FILE_KIDS_CODE,
            status=AUTOMATION_STATUS_READY,
            triggers=[],
        )

        # A deliberately-failing automation: each photo_indexed batch records a FAILED
        # run so the error path (and the run-history error display) can be eyeballed.
        error_automation = Automation(
            slug=_ERROR_SLUG,
            name="Always fails",
            description="Always raises an error — for testing the failed-run path.",
            is_system=False,
            enabled=True,
            handler=None,
            published_code=_ERROR_CODE,
            status=AUTOMATION_STATUS_READY,
            triggers=[AutomationTrigger(
                trigger_type=TRIGGER_TYPE_EVENT,
                enabled=True,
                event_type=EVENT_MEDIA_INDEXED,
            )],
        )
        seeded = [organize_automation, file_kids_automation, error_automation]
        db.session.add_all(seeded)
        db.session.commit()
        print("Seeded automations: " + ", ".join(f"'{a.slug}'" for a in seeded) + ".")


if __name__ == "__main__":
    seed_automations()
