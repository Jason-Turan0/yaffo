import yaffo.db.repositories.media_dir_repository
from yaffo.db import db
from pathlib import Path

from flask_babel import gettext

from yaffo.db.models import Automation, SYNC_ROLE_SOURCE, SYNC_ROLE_TARGET
from yaffo.sync.adapters import runnable_adapters
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


def _service_description(adapter) -> str:
    if adapter.is_builtin:
        return (gettext("A folder on this computer, a drive, or a card.") if adapter.role == SYNC_ROLE_SOURCE
                else gettext("A folder a desktop app syncs, a drive, a network share, or a card for a photo frame."))
    if adapter.slug == "preset.onedrive_source":
        return gettext("New photos and videos in a OneDrive folder. Sign in with your Microsoft account.")
    return adapter.metadata.get("description") or adapter.name


def _sync_services() -> dict[str, list[dict]]:
    """The services the New automation dialog offers for Import from… / Send to…:
    built-ins first, then presets."""
    services: dict[str, list[dict]] = {"import": [], "send": []}
    for kind, role in (("import", SYNC_ROLE_SOURCE), ("send", SYNC_ROLE_TARGET)):
        for adapter in runnable_adapters(db.session, role):
            services[kind].append({"id": adapter.id, "name": adapter.name,
                                   "description": _service_description(adapter)})
    return services