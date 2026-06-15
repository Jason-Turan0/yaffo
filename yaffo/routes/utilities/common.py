from yaffo.db import db
from pathlib import Path

from yaffo.db.models import Automation
from yaffo.utils import settings
from yaffo.utils.index_photos import is_system_file  # re-exported for route modules

__all__ = [
    "is_system_file", "get_media_dirs", "get_thumbnail_dir", "automations_sidebar_context",
]


def get_media_dirs() -> list[Path]:
    return settings.get_media_dirs(db.session)


def get_thumbnail_dir() -> Path | None:
    return settings.get_thumbnail_dir(db.session)


def automations_sidebar_context() -> dict:
    """The Automations panel data for the shared utilities sidebar
    (templates/utilities/_base.html). Every utilities page that extends the base
    spreads this into its render_template so the panel populates everywhere."""
    automations = db.session.query(Automation).order_by(Automation.name).all()
    return {
        "system_automations": [a for a in automations if a.is_system],
        "custom_automations": [a for a in automations if not a.is_system],
    }