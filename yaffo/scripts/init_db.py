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

    # Runtime-created cron schedules for background actions. A single dispatcher
    # (background_tasks/tasks/dispatcher.py) fires every minute, runs rows whose
    # next_run_at has passed, and advances next_run_at from `cron` -- so schedules
    # are fully dynamic with no consumer restart. `action` names a handler in
    # background_tasks/actions.py; `args` is its JSON payload.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS task_schedules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            key TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            action TEXT NOT NULL,
            args TEXT,
            enabled INTEGER NOT NULL DEFAULT 0,
            cron TEXT NOT NULL,
            next_run_at TIMESTAMP,
            last_run_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("""
        INSERT OR IGNORE INTO task_schedules (key, name, action, enabled, cron)
        VALUES ('file_sync', 'File sync', 'file_sync', 0, '0 * * * *')
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
                      version_id INTEGER NOT NULL,
                      type TEXT NOT NULL,
                      content TEXT NOT NULL,
                      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                      FOREIGN KEY(version_id) REFERENCES page_versions(id) ON DELETE CASCADE
                       )
                   """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_conversations_version_id ON conversations(version_id)")

    conn.commit()


if __name__ == "__main__":
    init_db()
