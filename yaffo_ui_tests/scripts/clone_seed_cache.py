#!/usr/bin/env python3
"""Clone a cached UI-test data directory and rebase its stored absolute paths."""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
from pathlib import Path


def _ignore_immutable_assets(source: Path):
    """Keep large read-only assets in the canonical cache, shared via YAFFO_ASSET_DIR."""
    source = source.resolve()

    def ignore(directory: str, names: list[str]) -> set[str]:
        if Path(directory).resolve() != source:
            return set()
        return {
            name for name in names
            if name in {"models", "ffmpeg"} or name.startswith("Image-ExifTool-")
        }

    return ignore


def _replace_prefix(connection: sqlite3.Connection, table: str, column: str,
                    source: str, destination: str) -> None:
    columns = {
        row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')
    }
    if column not in columns:
        return
    connection.execute(
        f'UPDATE "{table}" SET "{column}" = replace("{column}", ?, ?) '
        f'WHERE instr("{column}", ?) > 0',
        (source, destination, source),
    )


def rebase_database(database: Path, source: Path, destination: Path) -> None:
    """Rebase every application-owned field that stores a data-dir path."""
    source_text = str(source.resolve())
    destination_text = str(destination.resolve())
    with sqlite3.connect(database) as connection:
        _replace_prefix(connection, "media_items", "full_file_path", source_text, destination_text)
        _replace_prefix(connection, "media_items", "poster_path", source_text, destination_text)
        _replace_prefix(connection, "faces", "full_file_path", source_text, destination_text)
        thumbnail = connection.execute(
            "SELECT id, value FROM application_settings WHERE name = 'thumbnail_dir'"
        ).fetchone()
        if thumbnail and thumbnail[1]:
            connection.execute(
                "UPDATE application_settings SET value = ? WHERE id = ?",
                (thumbnail[1].replace(source_text, destination_text), thumbnail[0]),
            )

        media_dirs = connection.execute(
            "SELECT id, value FROM application_settings WHERE name = 'media_dirs'"
        ).fetchone()
        if media_dirs and media_dirs[1]:
            entries = json.loads(media_dirs[1])
            for entry in entries:
                if isinstance(entry, dict) and isinstance(entry.get("path"), str):
                    entry["path"] = entry["path"].replace(source_text, destination_text)
            connection.execute(
                "UPDATE application_settings SET value = ? WHERE id = ?",
                (json.dumps(entries), media_dirs[0]),
            )


def clone_seed_cache(source: Path, destination: Path) -> None:
    source = source.resolve()
    destination = destination.resolve()
    database = source / "yaffo.db"
    if not database.is_file():
        raise FileNotFoundError(f"Preseeded data dir has no yaffo.db: {source}")
    if destination.exists():
        raise FileExistsError(f"Clone destination already exists: {destination}")

    try:
        shutil.copytree(source, destination, ignore=_ignore_immutable_assets(source))
        rebase_database(destination / "yaffo.db", source, destination)
    except BaseException:
        shutil.rmtree(destination, ignore_errors=True)
        raise


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    clone_seed_cache(args.source, args.destination)


if __name__ == "__main__":
    main()
