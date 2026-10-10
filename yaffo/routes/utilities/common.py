import yaffo.db.repositories.media_dir_repository
from yaffo.db import db
from pathlib import Path

from yaffo.db.models import Automation, SyncAdapter, SYNC_ROLE_SOURCE
from yaffo.sync.adapters import BUILTINS
from yaffo.utils import settings
from yaffo.utils.index_photos import is_system_file  # re-exported for route modules

__all__ = [
    "is_system_file", "get_media_dirs", "get_thumbnail_dir", "automations_sidebar_context",
]


def get_media_dirs() -> list[Path]:
    return yaffo.db.repositories.media_dir_repository.get_media_dirs(db.session)


def get_thumbnail_dir() -> Path | None:
    return settings.get_thumbnail_dir(db.session)


def automations_sidebar_context() -> dict:
    """The Automations panel data for the shared utilities sidebar
    (templates/utilities/_base.html). Every utilities page that extends the base
    spreads this into its render_template so the panel populates everywhere."""
    automations = db.session.query(Automation).order_by(Automation.name).all()
    return {
        "system_automations": [a for a in automations if a.is_system],
        "custom_automations": [a for a in automations if not a.is_system and not a.is_sync],
        "sync_automations": [a for a in automations if a.is_sync],
        "sync_services": _sync_services(),
    }


def _sync_services() -> dict[str, list[dict]]:
    """The services the New automation dialog offers for Import from… / Send to…."""
    rows = db.session.query(SyncAdapter).order_by(SyncAdapter.is_preset.desc(), SyncAdapter.name).all()
    services: dict[str, list[dict]] = {"import": [], "send": []}
    for row in rows:
        if row.handler not in BUILTINS:
            continue
        services["import" if row.role == SYNC_ROLE_SOURCE else "send"].append({"id": row.id, "name": row.name})
    return services