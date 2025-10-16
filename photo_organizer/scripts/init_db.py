import sqlite3

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from photo_organizer.common import DB_PATH

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
            CREATE TABLE IF NOT EXISTS photos (
                id INTEGER PRIMARY KEY,
                full_file_path TEXT UNIQUE,
                relative_file_path TEXT UNIQUE,            
                hash TEXT,
                date_taken TEXT
            )
        """)
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
            FOREIGN KEY(photo_id) REFERENCES photos(id)
        )
    """)
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
                   face_id INTEGER,
                   similarity NUMERIC,
                   FOREIGN KEY(person_id) REFERENCES people(id),
                   FOREIGN KEY(face_id) REFERENCES faces(id),
                   UNIQUE(person_id, face_id)
               )
           """)
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
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()


if __name__ == "__main__":
    init_db()
