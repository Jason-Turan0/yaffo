"""Initial schema — the from-scratch baseline. Idempotent (every CREATE is
IF NOT EXISTS, every seed is INSERT OR IGNORE), so it's safe to apply to a
pre-existing database created before the migration table existed.

The runner manages the transaction and records this migration; do not open a
connection or commit here.
"""
import sqlite3

from yaffo.db.models import CLASSIFY_LABELS_DEFAULT_THRESHOLD, CLASSIFY_LABELS_DEFAULT_MAX

# Starter vocabulary for the classify-labels automation — common subjects, scenes,
# activities, and events in personal photos. Seeded as `is_default` rows; users
# add/remove/disable their own from Settings. Each entry is (name, prompt): the
# prompt is the phrase CLIP actually matches against, or None to use the default
# "a photo of <name>" template. Concrete nouns work fine bare; activities, events,
# and abstract terms match better with a fuller phrase.
DEFAULT_CLASSIFICATION_LABELS = [
    # Animals
    ("dog", None), ("cat", None), ("bird", None), ("horse", None),
    ("fish", None), ("rabbit", None),
    # Activities
    ("swimming", "people swimming in water"),
    ("hiking", "people hiking on a trail in nature"),
    ("skiing", "people skiing on snow"),
    ("running", "a person running outdoors"),
    ("cycling", "people riding bicycles"),
    ("dancing", "people dancing"),
    ("fishing", "a person fishing"),
    ("camping", "a campsite with tents"),
    ("surfing", "a person surfing on ocean waves"),
    ("playing sports", "people playing a sport"),
    # Nature & scenes
    ("beach", None), ("mountains", None), ("forest", None), ("lake", None),
    ("river", None), ("ocean", None), ("waterfall", None), ("garden", None),
    ("park", None), ("sunset", "a sunset sky"), ("snow", "a snowy winter scene"),
    ("autumn leaves", "autumn leaves in fall colors"),
    # Urban
    ("city skyline", "a city skyline"), ("street", "a city street"),
    ("building", None), ("bridge", None),
    # Events
    ("wedding", "a wedding ceremony"),
    ("birthday party", "a birthday party with a cake"),
    ("graduation", "a graduation ceremony with caps and gowns"),
    ("concert", "a live music concert"),
    ("parade", "a parade on a street"),
    ("fireworks", "fireworks in the night sky"),
    ("christmas", "a christmas celebration with decorations"),
    ("halloween", "halloween costumes and decorations"),
    # Food & drink
    ("food", None), ("coffee", None), ("dessert", None), ("cake", None),
    ("pizza", None), ("barbecue", "a barbecue cookout with grilled food"),
    ("wine", None),
    # People
    ("baby", None), ("children", None),
    ("group of people", "a group of people together"),
    ("selfie", "a selfie of a person"),
    ("portrait", "a portrait of a person"),
    ("crowd", "a large crowd of people"),
    # Objects & transport
    ("car", None), ("boat", None), ("airplane", None), ("bicycle", None),
    ("train", None), ("flowers", None), ("christmas tree", None),
    # Utility
    ("document", None), ("screenshot", None), ("artwork", None), ("map", None),
]


# @formatter:off
def migrate(conn: sqlite3.Connection) -> None:
    cursor = conn.cursor()
    cursor.execute("""
            CREATE TABLE IF NOT EXISTS photos (
                id INTEGER PRIMARY KEY,
                full_file_path TEXT UNIQUE,
                date_taken TEXT,
                year INTEGER,
                month INTEGER,
                status TEXT DEFAULT 'IMPORTED',
                latitude REAL,
                longitude REAL,
                location_name TEXT,
                device TEXT,
                favorite INTEGER
            )
        """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_photos_full_file_path ON photos(full_file_path)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_photos_date_taken ON photos(date_taken)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_photos_location_name ON photos(location_name)")

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS faces (
            id INTEGER PRIMARY KEY,
            embedding BLOB,
            full_file_path TEXT UNIQUE,
            photo_id INTEGER,
            status TEXT,
            location_top INTEGER,
            location_bottom INTEGER,
            location_left INTEGER,
            location_right INTEGER,
            estimated_age REAL,
            gender INTEGER,
            det_score REAL,
            FOREIGN KEY(photo_id) REFERENCES photos(id) ON DELETE CASCADE
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_face_photo_id ON faces(photo_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_face_status ON faces(status)")

    cursor.execute("""
           CREATE TABLE IF NOT EXISTS people (
               id INTEGER PRIMARY KEY,
               name TEXT,
               avg_embedding BLOB,
               birthdate DATE,
               estimated_birthdate DATE
           )
       """)



    cursor.execute("""
               CREATE TABLE IF NOT EXISTS people_face (
                   person_id INTEGER,
                   face_id INTEGER UNIQUE,
                   similarity NUMERIC,
                   FOREIGN KEY(person_id) REFERENCES people(id) ON DELETE CASCADE,
                   FOREIGN KEY(face_id) REFERENCES faces(id) ON DELETE CASCADE
               )
           """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_people_face_face_id ON people_face(face_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_people_face_person_id ON people_face(person_id)")

    cursor.execute("""
                CREATE TABLE IF NOT EXISTS people_embeddings (
                    person_id INTEGER NOT NULL,
                    life_stage TEXT NOT NULL,
                    avg_embedding BLOB NOT NULL,
                    included_face_ids TEXT,
                    PRIMARY KEY (person_id, life_stage),
                    FOREIGN KEY (person_id) REFERENCES people(id) ON DELETE CASCADE
                )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_people_embedding_person_id ON people_embeddings(person_id)")

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'PENDING',
            automation_id INTEGER REFERENCES automations(id) ON DELETE SET NULL,
            task_count INTEGER DEFAULT 0,
            completed_count INTEGER DEFAULT 0,
            cancelled_count INTEGER DEFAULT 0,
            error_count INTEGER DEFAULT 0,
            started_at TIMESTAMP,
            completed_at TIMESTAMP,
            error TEXT,
            message TEXT,
            job_data TEXT,
            job_result TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("""
                   CREATE TABLE IF NOT EXISTS job_results (
                       id INTEGER PRIMARY KEY AUTOINCREMENT,
                       job_id TEXT NOT NULL,
                       task_id TEXT NOT NULL,
                       result_data TEXT,
                       created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                       FOREIGN KEY (job_id) REFERENCES job(id) ON DELETE CASCADE
                   )
                   """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS tags (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            photo_id INTEGER NOT NULL,
            tag_name TEXT NOT NULL,
            tag_value TEXT,
            FOREIGN KEY(photo_id) REFERENCES photos(id) ON DELETE CASCADE
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_tags_photo_id ON tags(photo_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_tags_tag_name ON tags(tag_name)")

    # Curated vocabulary the classify-labels automation scores photos against
    # (zero-shot CLIP). User-editable from Settings; `prompt` overrides the default
    # "a photo of <name>" embedding text.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS classification_labels (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            prompt TEXT,
            enabled INTEGER NOT NULL DEFAULT 1,
            is_default INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    # Labels the classifier assigned to a photo, with the CLIP cosine confidence.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS photo_labels (
            photo_id INTEGER NOT NULL,
            label_id INTEGER NOT NULL,
            confidence REAL,
            PRIMARY KEY (photo_id, label_id),
            FOREIGN KEY(photo_id) REFERENCES photos(id) ON DELETE CASCADE,
            FOREIGN KEY(label_id) REFERENCES classification_labels(id) ON DELETE CASCADE
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_photo_labels_photo_id ON photo_labels(photo_id)")

    cursor.executemany(
        "INSERT OR IGNORE INTO classification_labels (name, prompt, is_default) VALUES (?, ?, 1)",
        DEFAULT_CLASSIFICATION_LABELS,
    )

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS application_settings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            type TEXT NOT NULL,
            value TEXT
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_application_settings_name ON application_settings(name)")

    # Automations: schedulable / event-driven units of functionality. System ones
    # (is_system=1) are code-backed via `handler`; custom ones carry AI-generated
    # `code`. A run of an automation reuses the jobs table (jobs.automation_id).
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS automations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            slug TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            description TEXT,
            is_system INTEGER NOT NULL DEFAULT 0,
            enabled INTEGER NOT NULL DEFAULT 0,
            handler TEXT,
            published_code TEXT,
            working_code TEXT,
            config TEXT,
            status TEXT NOT NULL DEFAULT 'READY',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    # When an automation runs: schedule triggers carry `cron` + the dispatcher's
    # next_run_at/last_run_at bookkeeping; event triggers carry `event_type`.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS automation_triggers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            automation_id INTEGER NOT NULL,
            trigger_type TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1,
            cron TEXT,
            next_run_at TIMESTAMP,
            last_run_at TIMESTAMP,
            event_type TEXT,
            config TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(automation_id) REFERENCES automations(id) ON DELETE CASCADE
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_automation_triggers_automation_id ON automation_triggers(automation_id)")

    # Seed the built-in file-sync automation (enabled) + its hourly schedule.
    cursor.execute("""
        INSERT OR IGNORE INTO automations (slug, name, description, is_system, enabled, handler, status)
        VALUES ('file_sync', 'File sync',
                'Reconcile the photo index with the files on disk.',
                1, 1, 'file_sync', 'READY')
    """)
    cursor.execute("""
        INSERT INTO automation_triggers (automation_id, trigger_type, enabled, cron)
        SELECT a.id, 'schedule', 1, '0 * * * *'
        FROM automations a
        WHERE a.slug = 'file_sync'
          AND NOT EXISTS (
              SELECT 1 FROM automation_triggers t
              WHERE t.automation_id = a.id AND t.trigger_type = 'schedule'
          )
    """)

    # Seed the built-in auto-assign-faces automation (enabled) + its photo_indexed
    # event trigger. config holds the tunable match threshold (see automation_config).
    cursor.execute("""
        INSERT OR IGNORE INTO automations (slug, name, description, is_system, enabled, handler, status, config)
        VALUES ('auto_assign_faces', 'Auto-assign faces',
                'When a photo is indexed, assign each detected face to the one person it matches above the threshold — a face matching several people is left unassigned.',
                1, 1, 'auto_assign_faces', 'READY', '{"threshold": 50}')
    """)
    cursor.execute("""
        INSERT INTO automation_triggers (automation_id, trigger_type, enabled, event_type)
        SELECT a.id, 'event', 1, 'photo_indexed'
        FROM automations a
        WHERE a.slug = 'auto_assign_faces'
          AND NOT EXISTS (
              SELECT 1 FROM automation_triggers t
              WHERE t.automation_id = a.id AND t.trigger_type = 'event'
          )
    """)

    # Seed the built-in export_photo_tag automation (enabled) + its photo_modified
    # event trigger. config holds which tags to export (see automation_config).
    cursor.execute("""
                   INSERT OR IGNORE INTO automations (slug, name, description, is_system, enabled, handler, status, config)
           VALUES ('export_photo_tag', 'Export photo tag',
                   'When a photo is modified, write its people and/or location tags back into the photo file.',
                   1, 1, 'export_photo_tag', 'READY', '{"export_location_tag_enabled": true, "export_people_tag_enabled": true}')
                   """)
    cursor.execute("""
                   INSERT INTO automation_triggers (automation_id, trigger_type, enabled, event_type)
                   SELECT a.id, 'event', 1, 'photo_modified'
                   FROM automations a
                   WHERE a.slug = 'export_photo_tag'
                     AND NOT EXISTS (
                       SELECT 1 FROM automation_triggers t
                       WHERE t.automation_id = a.id AND t.trigger_type = 'event'
                   )
                   """)
    cursor.execute("""
                   INSERT INTO automation_triggers (automation_id, trigger_type, enabled, event_type)
                   SELECT a.id, 'event', 1, 'photo_labeled'
                   FROM automations a
                   WHERE a.slug = 'export_photo_tag'
                     AND NOT EXISTS (
                       SELECT 1 FROM automation_triggers t
                       WHERE t.automation_id = a.id AND t.trigger_type = 'event'
                   )
                   """)

    # Seed the built-in assign-location-name automation (enabled) + its
    # photo_indexed event trigger. config holds the naming strategy + radius
    # (see automation_config).
    cursor.execute("""
        INSERT OR IGNORE INTO automations (slug, name, description, is_system, enabled, handler, status, config)
        VALUES ('assign_location_name', 'Assign location name',
                'When a photo is indexed, name its location from GPS — reuse a nearby named photo''s name, then fall back to an OpenStreetMap lookup.',
                1, 1, 'assign_location_name', 'READY',
                '{"reuse_nearby_enabled": true, "nearby_radius_meters": 10000, "reverse_geocode_enabled": false, "overwrite_existing": false}')
    """)
    cursor.execute("""
        INSERT INTO automation_triggers (automation_id, trigger_type, enabled, event_type)
        SELECT a.id, 'event', 1, 'photo_indexed'
        FROM automations a
        WHERE a.slug = 'assign_location_name'
          AND NOT EXISTS (
              SELECT 1 FROM automation_triggers t
              WHERE t.automation_id = a.id AND t.trigger_type = 'event'
          )
    """)

    # Seed the built-in geotag-from-neighbors automation (disabled) + its
    # photo_indexed event trigger. config holds the time window in minutes.
    cursor.execute("""
        INSERT OR IGNORE INTO automations (slug, name, description, is_system, enabled, handler, status, config)
        VALUES ('geotag_from_neighbors', 'Geotag from neighbors',
                'When a photo is indexed, give a GPS-less photo the coordinates of the closest-in-time photo that has GPS (e.g. a camera shot taken alongside phone shots).',
                1, 0, 'geotag_from_neighbors', 'READY', '{"max_minutes": 30}')
    """)
    cursor.execute("""
        INSERT INTO automation_triggers (automation_id, trigger_type, enabled, event_type)
        SELECT a.id, 'event', 1, 'photo_indexed'
        FROM automations a
        WHERE a.slug = 'geotag_from_neighbors'
          AND NOT EXISTS (
              SELECT 1 FROM automation_triggers t
              WHERE t.automation_id = a.id AND t.trigger_type = 'event'
          )
    """)

    # Seed the built-in classify-labels automation (enabled) + its photo_indexed
    # event trigger. config holds the 0-100 strictness threshold + per-photo label
    # cap (see automation_config); the label vocabulary lives in classification_labels.
    cursor.execute(f"""
        INSERT OR IGNORE INTO automations (slug, name, description, is_system, enabled, handler, status, config)
        VALUES ('classify_labels', 'Classify labels',
                'When a photo is indexed, label it with the subjects, scenes, and activities it matches from your label vocabulary (offline CLIP image recognition).',
                1, 1, 'classify_labels', 'READY',
                '{{"confidence_threshold": {CLASSIFY_LABELS_DEFAULT_THRESHOLD}, "max_labels": {CLASSIFY_LABELS_DEFAULT_MAX}}}')
    """)
    cursor.execute("""
        INSERT INTO automation_triggers (automation_id, trigger_type, enabled, event_type)
        SELECT a.id, 'event', 1, 'photo_indexed'
        FROM automations a
        WHERE a.slug = 'classify_labels'
          AND NOT EXISTS (
              SELECT 1 FROM automation_triggers t
              WHERE t.automation_id = a.id AND t.trigger_type = 'event'
          )
    """)

    # Seed the built-in duplicate-scan automation (disabled) + its daily schedule.
    cursor.execute("""
        INSERT OR IGNORE INTO automations (slug, name, description, is_system, enabled, handler, status)
        VALUES ('duplicate_scan', 'Duplicate scan',
                'Scan your indexed photos for duplicates on a schedule — results appear in the Remove Duplicates tool.',
                1, 0, 'duplicate_scan', 'READY')
    """)
    cursor.execute("""
        INSERT INTO automation_triggers (automation_id, trigger_type, enabled, cron)
        SELECT a.id, 'schedule', 1, '0 3 * * *'
        FROM automations a
        WHERE a.slug = 'duplicate_scan'
          AND NOT EXISTS (
              SELECT 1 FROM automation_triggers t
              WHERE t.automation_id = a.id AND t.trigger_type = 'schedule'
          )
    """)


    # The page builder is versioned: a page points at its live (published) version
    # and at most one in-flight (working) version; widgets + the conversation are
    # version-scoped. See docs/ai-page-builder.md.
    cursor.execute("""
                   CREATE TABLE IF NOT EXISTS custom_pages (
                       id INTEGER PRIMARY KEY AUTOINCREMENT,
                       title TEXT NOT NULL,
                       subtitle TEXT NOT NULL,
                       show_title INTEGER NOT NULL DEFAULT 1,
                       tab_order INTEGER NOT NULL DEFAULT 0,
                       published_version_id INTEGER,
                       working_version_id INTEGER,
                       created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                       updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                   )
                   """)

    cursor.execute("""
                   CREATE TABLE IF NOT EXISTS page_versions (
                       id INTEGER PRIMARY KEY AUTOINCREMENT,
                       page_id INTEGER NOT NULL,
                       status TEXT NOT NULL DEFAULT 'IN_PROGRESS',
                       parent_version_id INTEGER,
                       created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                       started_at TIMESTAMP,
                       completed_at TIMESTAMP,
                       error TEXT,
                       FOREIGN KEY(page_id) REFERENCES custom_pages(id) ON DELETE CASCADE
                   )
                   """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_page_versions_page_id ON page_versions(page_id)")

    cursor.execute("""
                   CREATE TABLE IF NOT EXISTS widgets (
                       id TEXT NOT NULL,
                       version_id INTEGER NOT NULL,
                       title TEXT NOT NULL DEFAULT "Untitled widget",
                       data_query TEXT,
                       state TEXT,
                       html TEXT,
                       css TEXT,
                       js TEXT,
                       grid_x INTEGER NOT NULL DEFAULT 0,
                       grid_y INTEGER NOT NULL DEFAULT 0,
                       grid_w INTEGER NOT NULL DEFAULT 4,
                       grid_h INTEGER NOT NULL DEFAULT 3,
                       PRIMARY KEY (id, version_id),
                       FOREIGN KEY(version_id) REFERENCES page_versions(id) ON DELETE CASCADE
                   )
                   """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_widgets_version_id ON widgets(version_id)")
    cursor.execute("""
                   CREATE TABLE IF NOT EXISTS conversations (
                      id INTEGER PRIMARY KEY AUTOINCREMENT,
                      version_id INTEGER,
                      automation_id INTEGER,
                      type TEXT NOT NULL,
                      content TEXT NOT NULL,
                      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                      FOREIGN KEY(version_id) REFERENCES page_versions(id) ON DELETE CASCADE,
                      FOREIGN KEY(automation_id) REFERENCES automations(id) ON DELETE CASCADE
                       )
                   """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_conversations_version_id ON conversations(version_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_conversations_automation_id ON conversations(automation_id)")
