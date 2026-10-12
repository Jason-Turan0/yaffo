"""The last successful model listing from each configured AI provider."""
from __future__ import annotations

import json

from sqlalchemy.orm import Session

from yaffo.db.models import ApplicationSettings

CATALOG_PREFIX = "llm_models:"


def catalogs(session: Session) -> dict[str, dict]:
    rows = session.query(ApplicationSettings.name, ApplicationSettings.value).filter(
        ApplicationSettings.name.startswith(CATALOG_PREFIX)).all()
    found = {}
    for name, raw in rows:
        try:
            value = json.loads(raw)
        except (TypeError, ValueError):
            continue
        if isinstance(value, dict):
            found[name[len(CATALOG_PREFIX):]] = value
    return found


def save(session: Session, provider_id: str, catalog: dict) -> None:
    name = CATALOG_PREFIX + provider_id
    row = session.query(ApplicationSettings).filter_by(name=name).first()
    value = json.dumps(catalog)
    if row is None:
        session.add(ApplicationSettings(name=name, type="json", value=value))
    else:
        row.value = value


def invalidate(session: Session, provider_id: str) -> None:
    catalog = catalogs(session).get(provider_id)
    if catalog is not None:
        save(session, provider_id, {**catalog, "refreshed_at": None})
