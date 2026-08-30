import json
import sqlite3
import subprocess
import sys
from pathlib import Path


def test_clone_seed_cache_copies_files_and_rebases_database_paths(tmp_path):
    source = tmp_path / "seed source"
    destination = tmp_path / "suite clone"
    source.mkdir()
    (source / "Family Photos").mkdir()
    (source / "Family Photos" / "photo.jpg").write_bytes(b"photo")
    (source / "models").mkdir()
    (source / "models" / "large-model.onnx").write_bytes(b"model")
    (source / "ffmpeg").mkdir()
    (source / "ffmpeg" / "ffmpeg").write_bytes(b"binary")
    (source / "Image-ExifTool-13.59").mkdir()

    database = source / "yaffo.db"
    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
            CREATE TABLE media_items (full_file_path TEXT, poster_path TEXT);
            CREATE TABLE faces (full_file_path TEXT);
            CREATE TABLE application_settings (id INTEGER PRIMARY KEY, name TEXT, value TEXT);
            """
        )
        connection.execute(
            "INSERT INTO media_items VALUES (?, ?)",
            (str(source / "Family Photos" / "photo.jpg"), str(source / "thumbnails" / "poster.jpg")),
        )
        connection.execute(
            "INSERT INTO faces VALUES (?)", (str(source / "thumbnails" / "face.jpg"),)
        )
        connection.execute(
            "INSERT INTO application_settings (name, value) VALUES ('thumbnail_dir', ?)",
            (str(source / "thumbnails"),),
        )
        connection.execute(
            "INSERT INTO application_settings (name, value) VALUES ('media_dirs', ?)",
            (json.dumps([{"id": "photos", "path": str(source / "Family Photos")}]),),
        )

    script = Path(__file__).parents[2] / "yaffo_ui_tests" / "scripts" / "clone_seed_cache.py"
    subprocess.run(
        [sys.executable, str(script), str(source), str(destination)],
        check=True,
    )

    assert (destination / "Family Photos" / "photo.jpg").read_bytes() == b"photo"
    assert not (destination / "models").exists()
    assert not (destination / "ffmpeg").exists()
    assert not (destination / "Image-ExifTool-13.59").exists()
    with sqlite3.connect(destination / "yaffo.db") as connection:
        media = connection.execute(
            "SELECT full_file_path, poster_path FROM media_items"
        ).fetchone()
        face = connection.execute("SELECT full_file_path FROM faces").fetchone()[0]
        settings = dict(connection.execute("SELECT name, value FROM application_settings"))

    assert media == (
        str(destination / "Family Photos" / "photo.jpg"),
        str(destination / "thumbnails" / "poster.jpg"),
    )
    assert face == str(destination / "thumbnails" / "face.jpg")
    assert settings["thumbnail_dir"] == str(destination / "thumbnails")
    assert json.loads(settings["media_dirs"])[0]["path"] == str(destination / "Family Photos")
