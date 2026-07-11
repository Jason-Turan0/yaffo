"""P2P device sharing: known_devices (paired peers) and share_grants
(per-peer scope authorizations). Mirrored in yaffo/db/models.py; see
docs/development/p2p-sharing.md. The album grant scope's album_id column
arrives with the albums tables in migration 007 (Phase 6).

The runner manages the transaction and records this migration; do not open a
connection or commit here.
"""
import sqlite3


def migrate(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS known_devices (
            device_id TEXT PRIMARY KEY,
            pubkey TEXT NOT NULL,
            display_name TEXT,
            trust_state TEXT NOT NULL DEFAULT 'trusted',
            paired_at TIMESTAMP,
            last_seen_at TIMESTAMP,
            revoked_at TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS share_grants (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            peer_device_id TEXT NOT NULL,
            scope_type TEXT NOT NULL,
            media_dir_id TEXT,
            relative_path TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            revoked_at TIMESTAMP,
            FOREIGN KEY (peer_device_id) REFERENCES known_devices(device_id) ON DELETE CASCADE
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_share_grants_peer_device_id ON share_grants(peer_device_id)"
    )
