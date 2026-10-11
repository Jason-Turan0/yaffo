"""Seed the built-in Peer source adapter: import from a paired Yaffo device
(docs/development/peer-sync.md). Like the Folder adapters, it is a row with a
`handler`; its code is in yaffo/sync/peer_source.py.

Data only: no schema change, nothing to mirror in 000_INIT (the sync tables come
from 016). Idempotent.
"""
import sqlite3

SEED_PEER_SOURCE = """
    INSERT OR IGNORE INTO sync_adapters (slug, name, role, provider, is_preset, preset_version, handler, status)
    VALUES ('builtin.peer_source', 'Paired device', 'source', 'peer', 1, '1', 'peer_source', 'ready')
"""


def migrate(conn: sqlite3.Connection) -> None:
    conn.execute(SEED_PEER_SOURCE)
