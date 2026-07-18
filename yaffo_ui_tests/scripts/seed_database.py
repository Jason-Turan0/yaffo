#!/usr/bin/env python3
"""
Seed the test database with photos from the test_data directory.

Usage:
    python seed_database.py

Requires YAFFO_DATA_DIR environment variable to be set.
"""

import importlib.util
import json
import os
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

from onnxruntime.transformers.profile_result_processor import process_results

from yaffo.background_tasks.tasks.classify_labels_automation import classify_media_items
from yaffo.common import MEDIA_TYPE_PHOTO, MEDIA_TYPE_VIDEO, PHOTO_EXTENSIONS
from yaffo.db.models import (
    Tag,
    Face,
    MediaItem,
    Person,
    CLASSIFY_LABELS_DEFAULT_MAX,
    CLASSIFY_LABELS_DEFAULT_THRESHOLD,
    FACE_STATUS_ASSIGNED,
    FACE_STATUS_IGNORED,
    FACE_STATUS_UNASSIGNED,
    MEDIA_STATUS_INDEXED,
)
from yaffo.db.repositories.person_repository import (
    bulk_link_faces_to_people,
    update_person_embedding,
)
from yaffo.db.repositories.media_dir_repository import add_media_dir
from yaffo.domain.compare_utils import serialize_embedding
from yaffo.download_assets import download_ffmpeg, download_exiftool, download_insightface, download_clip
from yaffo.utils.image_classifier import get_clip_threshold
from yaffo.utils.index_video import index_video

# Add yaffo project to path
YAFFO_PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(YAFFO_PROJECT_ROOT))
BENNETT_FACE_ASSIGNMENTS_PATH = (
    Path(__file__).parent.parent / "test_data" / "bennett_face_assignments.json"
)

SEED_PROFILE_BENNETT = "bennett"
SEED_PROFILE_OBAMA = "obama"
PersonSeed = tuple[str, int, date | None]

BENNETT_PEOPLE: tuple[PersonSeed, ...] = (
    ("Marcus Bennett", 1, None),
    ("Elena Bennett", 0, None),
    ("Maya Bennett", 0, date(2014, 9, 12)),
    ("Theo Bennett", 1, date(2017, 11, 15)),
)

OBAMA_PEOPLE: tuple[PersonSeed, ...] = (
    ("Barack Obama", 1, date(1961, 8, 4)),
    ("Michelle Obama", 0, date(1964, 1, 17)),
    ("Malia Obama", 0, date(1998, 7, 4)),
    ("Sasha Obama", 0, date(2001, 6, 10)),
)

# Exactly one face per selected photo is assigned to each person. The remaining
# ground-truth faces intentionally stay unassigned so the Faces page and its UI
# tests have realistic work available. Selections span the family's timeline;
# both children retain baby-era examples in their profiles.
BENNETT_SEEDED_FACE_PATHS = {
    "Marcus Bennett": {
        "2015_chicago_baby_trip/2015-10-09_103400_chicago-riverwalk.png",
        "2015_chicago_baby_trip/2015-10-09_151800_lakefront.png",
        "2015_chicago_baby_trip/2015-10-10_110700_neighborhood-walk.png",
        "2015_chicago_baby_trip/2015-10-11_085600_family-breakfast.png",
        "2017_third_birthday/2017-09-12_162200_blowing-candles.png",
        "2017_third_birthday/2017-09-12_165100_opening-gifts.png",
        "2017_third_birthday/2017-09-12_171400_birthday-candid.png",
        "2018_son_baby/2018-01-14_061800_bottle-with-dad.png",
        "2021_gulf_beach_trip/2021-07-10_195400_sunset-walk.png",
        "2026_present_day/2026-06-07_111500_family-at-home.png",
    },
    "Elena Bennett": {
        "2015_chicago_baby_trip/2015-10-09_103400_chicago-riverwalk.png",
        "2015_chicago_baby_trip/2015-10-09_151800_lakefront.png",
        "2015_chicago_baby_trip/2015-10-10_110700_neighborhood-walk.png",
        "2015_chicago_baby_trip/2015-10-11_085600_family-breakfast.png",
        "2017_third_birthday/2017-09-12_162200_blowing-candles.png",
        "2017_third_birthday/2017-09-12_165100_opening-gifts.png",
        "2017_third_birthday/2017-09-12_171400_birthday-candid.png",
        "2018_son_baby/2018-06-24_193200_story-time.png",
        "2021_gulf_beach_trip/2021-07-10_195400_sunset-walk.png",
        "2026_present_day/2026-06-07_111500_family-at-home.png",
    },
    "Maya Bennett": {
        "2015_daughter_baby/2015-09-10_153200_daughter-one-year-portrait.png",
        "2015_chicago_baby_trip/2015-10-09_103400_chicago-riverwalk.png",
        "2015_chicago_baby_trip/2015-10-09_151800_lakefront.png",
        "2015_chicago_baby_trip/2015-10-11_085600_family-breakfast.png",
        "2017_third_birthday/2017-09-12_162200_blowing-candles.png",
        "2017_third_birthday/2017-09-12_171400_birthday-candid.png",
        "2018_son_baby/2018-04-22_103600_tummy-time-with-sister.png",
        "2021_gulf_beach_trip/2021-07-11_101300_collecting-shells.png",
        "2021_gulf_beach_trip/2021-07-11_112200_chasing-sandpipers.png",
        "2026_present_day/2026-06-07_111500_family-at-home.png",
    },
    "Theo Bennett": {
        "2018_son_baby/2018-01-14_061800_bottle-with-dad.png",
        "2018_son_baby/2018-04-22_103600_tummy-time-with-sister.png",
        "2018_son_baby/2018-06-24_193200_story-time.png",
        "2018_son_baby/2018-09-15_142800_son-ten-month-portrait.png",
        "2018_son_baby/2018-09-22_101900_crawling-with-family.png",
        "2021_gulf_beach_trip/2021-07-10_101800_beach-arrival.png",
        "2021_gulf_beach_trip/2021-07-11_104800_sand-moat.png",
        "2021_gulf_beach_trip/2021-07-11_113109_boy-runs-into-waves.png",
        "2021_gulf_beach_trip/2021-07-11_113119_boy-runs-out.png",
        "2021_gulf_beach_trip/2021-07-11_173600_sand-drawing.png",
    },
}


def seed_people(db, people: tuple[PersonSeed, ...], profile_label: str) -> None:
    existing_names = {
        name for (name,) in db.session.query(Person.name).all()
    }
    added = 0
    for name, gender, birthdate in people:
        if name in existing_names:
            continue
        db.session.add(Person(name=name, gender=gender, birthdate=birthdate))
        added += 1
    db.session.commit()
    print(f"  Seeded {profile_label} people: {added} added ({len(people)} expected)")


def _bbox_iou(left: list[int], right: list[int]) -> float:
    left_top, left_right, left_bottom, left_left = left
    right_top, right_right, right_bottom, right_left = right
    intersection_width = max(0, min(left_right, right_right) - max(left_left, right_left))
    intersection_height = max(0, min(left_bottom, right_bottom) - max(left_top, right_top))
    intersection = intersection_width * intersection_height
    left_area = max(0, left_right - left_left) * max(0, left_bottom - left_top)
    right_area = max(0, right_right - right_left) * max(0, right_bottom - right_top)
    union = left_area + right_area - intersection
    return intersection / union if union else 0.0


def seed_bennett_face_assignments(db, photos_dir: Path) -> None:
    annotations = json.loads(BENNETT_FACE_ASSIGNMENTS_PATH.read_text(encoding="utf-8"))
    annotations_by_path = defaultdict(list)
    for annotation in annotations:
        annotations_by_path[annotation["path"]].append(annotation)

    media_items = db.session.query(MediaItem).filter(MediaItem.media_type == MEDIA_TYPE_PHOTO).all()
    media_by_path = {
        Path(media.full_file_path).relative_to(photos_dir).as_posix(): media
        for media in media_items
    }
    people_by_name = {
        person.name: person for person in db.session.query(Person).all()
    }

    links: list[tuple[int, int]] = []
    assigned_paths = defaultdict(set)
    matched_face_ids: set[int] = set()
    missing: list[str] = []

    for relative_path, path_annotations in annotations_by_path.items():
        media = media_by_path.get(relative_path)
        if media is None:
            missing.append(f"missing media: {relative_path}")
            continue
        available_faces = [face for face in media.faces if face.id not in matched_face_ids]
        for annotation in path_annotations:
            candidates = sorted(
                (
                    (
                        _bbox_iou(
                            annotation["bbox"],
                            [
                                face.location_top,
                                face.location_right,
                                face.location_bottom,
                                face.location_left,
                            ],
                        ),
                        face,
                    )
                    for face in available_faces
                ),
                key=lambda candidate: candidate[0],
                reverse=True,
            )
            if not candidates or candidates[0][0] < 0.75:
                missing.append(f"unmatched face: {relative_path} {annotation['bbox']}")
                continue
            _, face = candidates[0]
            available_faces.remove(face)
            matched_face_ids.add(face.id)
            if annotation["status"] == FACE_STATUS_IGNORED:
                # Keep ignored ground-truth entries only for validating that
                # face detection is stable. The demo seed leaves them in the
                # assignment pool along with every non-selected family face.
                face.status = FACE_STATUS_UNASSIGNED
                continue
            person = people_by_name.get(annotation["person"])
            if person is None:
                missing.append(f"missing person: {annotation['person']}")
                continue
            selected_paths = BENNETT_SEEDED_FACE_PATHS[person.name]
            if relative_path in selected_paths and relative_path not in assigned_paths[person.name]:
                face.status = FACE_STATUS_ASSIGNED
                links.append((person.id, face.id))
                assigned_paths[person.name].add(relative_path)
            else:
                face.status = FACE_STATUS_UNASSIGNED

    detected_face_ids = {
        face_id for (face_id,) in db.session.query(Face.id).all()
    }
    if missing or matched_face_ids != detected_face_ids:
        unmatched_detected = sorted(detected_face_ids - matched_face_ids)
        details = "; ".join(missing + [f"unannotated detected face ids: {unmatched_detected}"])
        raise RuntimeError(f"Bennett face fixture no longer matches detection output: {details}")

    bulk_link_faces_to_people(db.session, links)
    for person_name, _gender, _birthdate in BENNETT_PEOPLE:
        update_person_embedding(people_by_name[person_name].id, db.session)

    counts = Counter(
        person.name
        for person_id, _face_id in links
        for person in [db.session.get(Person, person_id)]
        if person is not None
    )
    expected_counts = {name: 10 for name, _gender, _birthdate in BENNETT_PEOPLE}
    if dict(counts) != expected_counts:
        raise RuntimeError(f"Bennett fixture requires exactly 10 seeded faces per person: {dict(counts)}")
    for person_name, selected_paths in BENNETT_SEEDED_FACE_PATHS.items():
        if assigned_paths[person_name] != selected_paths:
            raise RuntimeError(f"Bennett fixture did not seed every selected scene for {person_name}")
    baby_requirements = {
        "Maya Bennett": "2015_daughter_baby/",
        "Theo Bennett": "2018_son_baby/",
    }
    for person_name, prefix in baby_requirements.items():
        if not any(path.startswith(prefix) for path in assigned_paths[person_name]):
            raise RuntimeError(f"Bennett fixture requires a baby photo for {person_name}")
    unassigned_count = len(annotations) - len(links)
    print(
        f"  Seeded Bennett faces: {dict(counts)}; "
        f"left unassigned {unassigned_count}"
    )


def seed_media_labels(db) -> None:
    media_item_ids = [
        media_id for (media_id,) in
        db.session.query(MediaItem.id)
        .filter(MediaItem.media_type == MEDIA_TYPE_PHOTO)
        .order_by(MediaItem.id)
        .all()
    ]
    labeled = classify_media_items(
        db.session,
        media_item_ids,
        get_clip_threshold(CLASSIFY_LABELS_DEFAULT_THRESHOLD),
        CLASSIFY_LABELS_DEFAULT_MAX,
    )
    print(f"  Seeded classification labels: {len(labeled)} of {len(media_item_ids)} photos labeled")


def _load_default_classification_labels() -> list[tuple]:
    """Load DEFAULT_CLASSIFICATION_LABELS from the INIT migration so the test
    vocabulary stays in sync with what a fresh install seeds (the migration module
    name starts with a digit, so import it by path like migrate.py does)."""
    path = YAFFO_PROJECT_ROOT / "yaffo" / "scripts" / "db" / "migrations" / "000_MIGRATION_20260620_INIT.py"
    spec = importlib.util.spec_from_file_location("_yaffo_init_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.DEFAULT_CLASSIFICATION_LABELS


def seed_system_automations(db) -> None:
    """Seed the built-in system automations + triggers, mirroring what the
    production migrations seed (000_INIT, with events renamed by 002)."""
    from yaffo.db.models import (
        Automation,
        AutomationTrigger,
        AUTOMATION_STATUS_READY,
        AUTOMATION_HANDLER_FILE_SYNC,
        AUTOMATION_HANDLER_AUTO_ASSIGN_FACES,
        AUTOMATION_HANDLER_EXPORT_PHOTO_TAG,
        AUTOMATION_HANDLER_ASSIGN_LOCATION_NAME,
        AUTOMATION_HANDLER_GEOTAG_FROM_NEIGHBORS,
        AUTOMATION_HANDLER_CLASSIFY_LABELS,
        AUTOMATION_HANDLER_DUPLICATE_SCAN,
        GEOTAG_FROM_NEIGHBORS_DEFAULT_MINUTES,
        CLASSIFY_LABELS_DEFAULT_THRESHOLD,
        CLASSIFY_LABELS_DEFAULT_MAX,
        EVENT_MEDIA_INDEXED,
        EVENT_MEDIA_MODIFIED,
        SYSTEM_AUTOMATION_TEXT,
    )

    # (handler/slug, enabled, config, triggers as (trigger_type, cron, event_type)).
    # Values mirror the INIT migration's seeds; the DB stores the English
    # name/description (SYSTEM_AUTOMATION_TEXT localizes them in the UI).
    system_automations = [
        (AUTOMATION_HANDLER_FILE_SYNC, True, None,
         [("schedule", "0 * * * *", None)]),
        # The INIT migration seeds threshold 50 (the code-side fallback
        # AUTO_ASSIGN_FACES_DEFAULT_THRESHOLD is 80) — mirror the migration.
        (AUTOMATION_HANDLER_AUTO_ASSIGN_FACES, True,
         {"threshold": 50},
         [("event", None, EVENT_MEDIA_INDEXED)]),
        (AUTOMATION_HANDLER_EXPORT_PHOTO_TAG, True,
         {"export_location_tag_enabled": True, "export_people_tag_enabled": True},
         [("event", None, EVENT_MEDIA_MODIFIED)]),
        (AUTOMATION_HANDLER_ASSIGN_LOCATION_NAME, True,
         {"reuse_nearby_enabled": True, "nearby_radius": 10, "nearby_radius_unit": "km",
          "nearby_radius_kilometers": 10, "reverse_geocode_enabled": False,
          "overwrite_existing": False},
         [("event", None, EVENT_MEDIA_INDEXED)]),
        (AUTOMATION_HANDLER_GEOTAG_FROM_NEIGHBORS, False,
         {"max_minutes": GEOTAG_FROM_NEIGHBORS_DEFAULT_MINUTES},
         [("event", None, EVENT_MEDIA_INDEXED)]),
        (AUTOMATION_HANDLER_CLASSIFY_LABELS, True,
         {"confidence_threshold": CLASSIFY_LABELS_DEFAULT_THRESHOLD,
          "max_labels": CLASSIFY_LABELS_DEFAULT_MAX},
         [("event", None, EVENT_MEDIA_INDEXED)]),
        (AUTOMATION_HANDLER_DUPLICATE_SCAN, False, None,
         [("schedule", "0 3 * * *", None)]),
    ]

    for handler, enabled, config, triggers in system_automations:
        if db.session.query(Automation).filter(Automation.slug == handler).first():
            continue
        name, description = SYSTEM_AUTOMATION_TEXT[handler]
        automation = Automation(
            slug=handler,
            name=str(name),
            description=str(description),
            is_system=True,
            enabled=enabled,
            handler=handler,
            config=config,
            status=AUTOMATION_STATUS_READY,
        )
        db.session.add(automation)
        db.session.flush()
        for trigger_type, cron, event_type in triggers:
            db.session.add(AutomationTrigger(
                automation_id=automation.id,
                trigger_type=trigger_type,
                enabled=True,
                cron=cron,
                event_type=event_type,
            ))
        print(f"  Seeded system automation: {handler} (enabled={enabled})")
    db.session.commit()


def seed_classification_labels(db) -> None:
    """Seed the default classification-label vocabulary the classify-labels
    automation scores photos against (same starter set a fresh install gets)."""
    from yaffo.db.models import ClassificationLabel

    existing = {name for (name,) in db.session.query(ClassificationLabel.name).all()}
    added = 0
    for name, prompt in _load_default_classification_labels():
        if name in existing:
            continue
        db.session.add(ClassificationLabel(name=name, prompt=prompt, enabled=True, is_default=True))
        added += 1
    db.session.commit()
    total = db.session.query(ClassificationLabel).count()
    print(f"  Seeded classification labels: {added} added ({total} total)")


def seed_custom_automations(db) -> None:
    """Seed a couple of custom (AI-authored, Starlark-backed) automations so the
    automations page has non-system entries with code, triggers, and a chat
    transcript to exercise."""
    from yaffo.background_tasks.automation_sandbox.starlark_runner import validate_starlark
    from yaffo.db.models import (
        Automation,
        AutomationTrigger,
        Conversation,
        AUTOMATION_STATUS_READY,
        CONVERSATION_TYPE_USER,
        CONVERSATION_TYPE_ASSISTANT,
        EVENT_MEDIA_INDEXED,
    )

    tag_recent_code = (
        '# Tag the ten most recently indexed photos so they are easy to find.\n'
        'rows = data_query({"source": "media_items", "limit": 10})\n'
        'tag_media_items([{"media_item_id": r["id"], "name": "recent-import"} for r in rows])\n'
        'print("Tagged %d photos" % len(rows))\n'
    )
    tag_arrivals_code = (
        '# Tag newly indexed photos as they arrive.\n'
        'ids = ctx["media_item_ids"]\n'
        'if len(ids) > 0:\n'
        '    tag_media_items([{"media_item_id": pid, "name": "new-arrival"} for pid in ids])\n'
        'print("Processed %d new photos" % len(ids))\n'
    )
    tag_arrivals_draft = (
        '# Draft: also report progress while tagging new arrivals.\n'
        'ids = ctx["media_item_ids"]\n'
        'if len(ids) > 0:\n'
        '    tag_media_items([{"media_item_id": pid, "name": "new-arrival"} for pid in ids])\n'
        '    report_progress(len(ids), len(ids))\n'
        'print("Processed %d new photos" % len(ids))\n'
    )

    # (slug, name, description, enabled, published, working_draft, triggers, chat)
    custom_automations = [
        (
            "tag-recent-imports",
            "Tag recent imports",
            "Every night, tag the ten most recently indexed photos with 'recent-import'.",
            True,
            tag_recent_code,
            None,
            [("schedule", "0 4 * * *", None)],
            [
                (CONVERSATION_TYPE_USER,
                 "Tag the 10 most recently indexed photos with 'recent-import' every night."),
                (CONVERSATION_TYPE_ASSISTANT,
                 "I created an automation that queries the ten most recent photos and tags "
                 "them 'recent-import'. I scheduled it to run daily at 4:00 AM."),
            ],
        ),
        (
            "tag-new-arrivals",
            "Tag new arrivals",
            "When photos are indexed, tag them with 'new-arrival'. Disabled by default.",
            False,
            tag_arrivals_code,
            tag_arrivals_draft,
            [("event", None, EVENT_MEDIA_INDEXED)],
            [
                (CONVERSATION_TYPE_USER, "Tag every newly indexed photo with 'new-arrival'."),
                (CONVERSATION_TYPE_ASSISTANT,
                 "Done — the automation reads the indexed photo ids from the trigger context "
                 "and tags each one 'new-arrival'. I left it disabled so you can review it first."),
            ],
        ),
    ]

    for slug, name, description, enabled, published, draft, triggers, chat in custom_automations:
        if db.session.query(Automation).filter(Automation.slug == slug).first():
            continue
        for label, code in (("published", published), ("working", draft)):
            error = validate_starlark(code) if code else None
            if error:
                raise ValueError(f"Seed automation {slug} has invalid {label} Starlark: {error}")
        automation = Automation(
            slug=slug,
            name=name,
            description=description,
            is_system=False,
            enabled=enabled,
            handler=None,
            published_code=published,
            working_code=draft,
            status=AUTOMATION_STATUS_READY,
        )
        db.session.add(automation)
        db.session.flush()
        for trigger_type, cron, event_type in triggers:
            db.session.add(AutomationTrigger(
                automation_id=automation.id,
                trigger_type=trigger_type,
                enabled=True,
                cron=cron,
                event_type=event_type,
            ))
        for entry_type, content in chat:
            db.session.add(Conversation(
                automation_id=automation.id, type=entry_type, content=content,
            ))
        print(f"  Seeded custom automation: {slug} (enabled={enabled})")
    db.session.commit()


def seed_custom_themes(db) -> None:
    """Seed one published custom theme (stored whole in ApplicationSettings as
    custom_theme:<slug>) so the themes page has a deletable, non-built-in entry."""
    from yaffo import themes
    from yaffo.db.models import PAGE_VERSION_STATUS_ACCEPTED

    slug = "test-ocean"
    if themes.get_custom_theme(slug, db.session) is not None:
        return

    tokens_css = (
        f'[data-theme="{slug}"] {{\n'
        '    --color-bg: #eaf4f8;\n'
        '    --color-surface: #f7fbfd;\n'
        '    --color-surface-hover: #e2eff5;\n'
        '    --color-text: #123240;\n'
        '    --color-text-secondary: #33566a;\n'
        '    --color-border: #b7d3de;\n'
        '}\n'
    )
    skin_css = (
        f'[data-theme="{slug}"] .navbar {{\n'
        '    border-bottom: 2px solid #b7d3de;\n'
        '}\n'
    )
    now = datetime.now(timezone.utc).isoformat()
    theme = themes.CustomTheme(
        slug=slug,
        status=PAGE_VERSION_STATUS_ACCEPTED,
        label="Test Ocean",
        conversations=[
            themes.ThemeConversation(
                type="user", content="Make me a calm, ocean-inspired light theme.", created_at=now,
            ),
            themes.ThemeConversation(
                type="assistant",
                content="I built Test Ocean: a pale sea-blue page with deep teal text and soft coastal borders.",
                created_at=now,
            ),
        ],
        published_theme=themes.ThemeAssets(tokens_css=tokens_css, skin_css=skin_css),
        working_theme=None,
    )
    themes.save_custom_theme(theme, db.session)
    print(f"  Seeded custom theme: {slug}")


def seed_custom_pages(db, seed_profile: str) -> None:
    """Seed two published custom pages with widgets so the pages nav strip,
    presentation view, and design view have content to exercise."""
    from yaffo.db.models import CustomPage
    from yaffo.db.repositories import custom_page_repository as pages

    if db.session.query(CustomPage).filter(CustomPage.title == "Favorites Wall").first():
        return

    page = pages.create_page(db.session, "Favorites Wall", "A seeded page for UI tests")
    pages.save_page_widgets(db.session, page.id, [
        {
            "id": pages.new_widget_id(),
            "title": "Welcome",
            "data_query": {},
            "html": '<div class="welcome"><h2>Welcome to the Favorites Wall</h2>'
                    '<p>This page was seeded for UI testing.</p></div>',
            "css": '.welcome { padding: 8px; } .welcome h2 { margin: 0 0 4px; }',
            "js": "",
            "grid_x": 0, "grid_y": 0, "grid_w": 6, "grid_h": 2,
        },
        {
            "id": pages.new_widget_id(),
            "title": "Recent photos",
            "data_query": {"recent_photos": {"source": "media_items", "limit": 6}},
            "html": '<div class="recent"><span id="recent-count">0</span> recent photos</div>',
            "css": '.recent { padding: 8px; font-size: 18px; }',
            "js": "var rows = (yaffo.data && yaffo.data.recent_photos) || [];\n"
                  "document.getElementById('recent-count').textContent = String(rows.length);",
            "grid_x": 6, "grid_y": 0, "grid_w": 6, "grid_h": 2,
        },
    ])
    print(f"  Seeded custom page: Favorites Wall (id={page.id})")

    about_text = (
        "Seeded test library of synthetic Bennett family photos."
        if seed_profile == SEED_PROFILE_BENNETT
        else "Seeded peer library of Obama family photos from the Obama Presidential Library."
    )
    about = pages.create_page(db.session, "About", "")
    pages.save_page_widgets(db.session, about.id, [
        {
            "id": pages.new_widget_id(),
            "title": "About this library",
            "data_query": {},
            "html": f'<div class="about"><p>{about_text}</p></div>',
            "css": '.about { padding: 8px; }',
            "js": "",
            "grid_x": 0, "grid_y": 0, "grid_w": 12, "grid_h": 2,
        },
    ])
    print(f"  Seeded custom page: About (id={about.id})")


def seed_albums(db) -> None:
    """One album with a few members, so album screens have something to show without a
    test having to build one first. Runs AFTER indexing — an album needs media items."""
    from yaffo.db.models import MediaItem
    from yaffo.db.repositories import album_repository

    if album_repository.list_albums(db.session):
        return
    photo_ids = [
        row[0]
        for row in db.session.query(MediaItem.id)
        .order_by(MediaItem.date_taken.desc())
        .limit(4)
        .all()
    ]
    if not photo_ids:
        print("  Skipped album seed: no indexed media items")
        return
    album = album_repository.create_album(
        db.session, "Seeded Album", description="Seeded for UI tests"
    )
    album_repository.add_items(db.session, album.id, photo_ids)
    print(f"  Seeded album: Seeded Album ({len(photo_ids)} photos)")


def seed_database() -> int:
    """Index test photos and seed the database. Returns count of photos indexed."""
    data_dir = os.environ.get("YAFFO_DATA_DIR")
    if not data_dir:
        print("Error: YAFFO_DATA_DIR environment variable not set")
        sys.exit(1)

    data_dir = Path(data_dir)
    photos_dir = data_dir / "organized"
    thumbnail_dir = data_dir / "thumbnails"

    from yaffo.app import create_app
    from yaffo.db import db
    from yaffo.db.models import ApplicationSettings, MediaItem
    from yaffo.utils.index_photos import index_photo

    app = create_app()
    seed_profile = os.environ.get("YAFFO_SEED_PROFILE", SEED_PROFILE_BENNETT)
    if seed_profile not in {SEED_PROFILE_BENNETT, SEED_PROFILE_OBAMA}:
        raise ValueError(f"Unsupported YAFFO_SEED_PROFILE: {seed_profile}")

    with app.app_context():
        db.create_all()

        # Seed application settings
        thumbnail_setting = ApplicationSettings(
            name="thumbnail_dir",
            type="str",
            value=str(thumbnail_dir),
        )
        db.session.add(thumbnail_setting)
        add_media_dir(db.session, str(photos_dir))

        db.session.commit()
        print(f"  Created settings: thumbnail_dir={thumbnail_dir}")
        print(f"  Created settings: media_dirs=[{photos_dir}]")

        # Seed the built-in system automations and the default label vocabulary
        # (in production these come from the DB migrations, which the test
        # sandbox skips by using db.create_all()).
        seed_system_automations(db)
        seed_classification_labels(db)
        seed_custom_automations(db)
        seed_custom_themes(db)
        seed_custom_pages(db, seed_profile)
        if seed_profile == SEED_PROFILE_BENNETT:
            seed_people(db, BENNETT_PEOPLE, "Bennett")
        else:
            seed_people(db, OBAMA_PEOPLE, "Obama")
        download_ffmpeg()
        download_exiftool()
        download_insightface()
        download_clip()

        # Index photos
        indexed_count = 0
        processed_results = []
        # Bennett is organized into nested event folders and uses PNG, while
        # the peer's Obama fixture uses JPEG. Index every format the app
        # supports and sort by basename for deterministic media/face ids.
        photo_paths = (
            path for path in photos_dir.rglob("*")
            if path.is_file() and path.suffix.lower() in PHOTO_EXTENSIONS
        )
        for photo_path in sorted(photo_paths, key=lambda path: path.name.lower()):
            try:
                indexed_photo = index_photo(photo_path, thumbnail_dir)
                processed_results.append(indexed_photo)
                print(f"  Indexed: {photo_path.name}")
            except Exception as e:
                print(f"  Error indexing {photo_path.name}: {e}")

        for video_path in sorted(photos_dir.glob("*.mp4"), key=lambda path: path.name.lower()):
            try:
                indexed_video = index_video(video_path, thumbnail_dir)
                processed_results.append(indexed_video)
                print(f"  Indexed: {video_path.name}")
            except Exception as e:
                print(f"  Error indexing {video_path.name}: {e}")


        for index_result in processed_results:
            media_item = MediaItem()
            faces_data = index_result["faces_data"]
            latitude = index_result["latitude"]
            longitude = index_result["longitude"]
            location_name = index_result["location_name"]
            media_item.device = index_result["device"]
            media_item.latitude = latitude
            media_item.longitude = longitude
            media_item.location_name = location_name
            media_item.date_taken = index_result["date_taken"]
            media_item.year = index_result["year"]
            media_item.month = index_result["month"]
            media_item.full_file_path = index_result["full_file_path"]
            if index_result.get("media_type") == MEDIA_TYPE_VIDEO:
                media_item.duration_seconds = index_result.get("duration_seconds")
                media_item.height = index_result.get("height")
                media_item.width = index_result.get("width")
                media_item.video_codec = index_result.get("video_codec")
                media_item.poster_path = index_result.get("poster_path")
                media_item.media_type = index_result.get("media_type")
            media_item.status = MEDIA_STATUS_INDEXED
            db.session.add(media_item)
            db.session.flush()

            for face_data in faces_data:
                face = Face(
                    embedding=serialize_embedding(face_data['embedding']),
                    full_file_path=face_data['full_file_path'],
                    status=FACE_STATUS_UNASSIGNED,
                    media_item_id=media_item.id,
                    location_top=face_data['location_top'],
                    location_right=face_data['location_right'],
                    location_bottom=face_data['location_bottom'],
                    location_left=face_data['location_left'],
                    estimated_age=face_data.get('estimated_age'),
                    gender=face_data.get('gender'),
                    det_score=face_data.get('det_score'),
                )
                db.session.add(face)


        db.session.commit()

        if seed_profile == SEED_PROFILE_BENNETT:
            seed_bennett_face_assignments(db, photos_dir)
        seed_media_labels(db)

        # After indexing: an album needs media items to hold.
        seed_albums(db)

        total = db.session.query(MediaItem).count()
        print(f"  Total media items in database: {total}")

        return indexed_count


if __name__ == "__main__":
    seed_database()
