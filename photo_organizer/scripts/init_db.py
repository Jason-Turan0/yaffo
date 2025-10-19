import sqlite3
from photo_organizer.common import DB_PATH


# @formatter:off
def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
            CREATE TABLE IF NOT EXISTS photos (
                id INTEGER PRIMARY KEY,
                full_file_path TEXT UNIQUE,
                relative_file_path TEXT,
                hash TEXT,
                date_taken TEXT,
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
            relative_file_path TEXT UNIQUE,
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
    conn.commit()


if __name__ == "__main__":
    init_db()
