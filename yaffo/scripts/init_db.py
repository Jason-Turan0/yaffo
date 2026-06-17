import sqlite3
from yaffo.common import DB_PATH


# @formatter:off
def init_db():
    conn = sqlite3.connect(DB_PATH)
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
                location_name TEXT
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
            FOREIGN KEY(photo_id) REFERENCES photos(id) ON DELETE CASCADE
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_face_photo_id ON faces(photo_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_face_status ON faces(status)")

    cursor.execute("""
           CREATE TABLE IF NOT EXISTS people (
               id INTEGER PRIMARY KEY,
               name TEXT,
               avg_embedding BLOB
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
                    year INTEGER NOT NULL,
                    avg_embedding BLOB NOT NULL,
                    included_face_ids TEXT,
                    PRIMARY KEY (person_id, year),
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
                       huey_task_id TEXT NOT NULL,
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

    # Seed the built-in file-sync automation (disabled) + its hourly schedule.
    cursor.execute("""
        INSERT OR IGNORE INTO automations (slug, name, description, is_system, enabled, handler, status)
        VALUES ('file_sync', 'File sync',
                'Reconcile the photo index with the files on disk.',
                1, 0, 'file_sync', 'READY')
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

    # Seed the built-in auto-assign-faces automation (disabled) + its photo_indexed
    # event trigger. config holds the tunable match threshold (see automation_config).
    cursor.execute("""
        INSERT OR IGNORE INTO automations (slug, name, description, is_system, enabled, handler, status, config)
        VALUES ('auto_assign_faces', 'Auto-assign faces',
                'When a photo is indexed, assign each detected face to the one person it matches above the threshold — a face matching several people is left unassigned.',
                1, 0, 'auto_assign_faces', 'READY', '{"threshold": 0.95}')
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
    # version-scoped. See docs/ai-page-builder-async-generation.md.
    cursor.execute("""
                   CREATE TABLE IF NOT EXISTS custom_pages (
                       id INTEGER PRIMARY KEY AUTOINCREMENT,
                       title TEXT NOT NULL,
                       subtitle TEXT NOT NULL,
                       show_title INTEGER NOT NULL DEFAULT 1,
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

    conn.commit()


if __name__ == "__main__":
    init_db()
