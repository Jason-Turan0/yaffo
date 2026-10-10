"""Preset adapters: Starlark adapters written and reviewed by us, shipped as
`yaffo/sync/presets/*.star` and seeded into `sync_adapters`.

Seeding is idempotent and runs whenever adapters are listed or loaded, so an app
upgrade re-seeds on first use. A preset whose `preset_version` or code changed gets
its new code, metadata, manifest, and grants (presets ship with their grants,
approved by the review that shipped them). The consent of syncs using a preset
covers its grant set (see consent.py), so a fix that doesn't widen what the adapter
may call doesn't turn them off, and one that does asks again.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from yaffo.common import BUNDLE_ROOT
from yaffo.db.models import SYNC_ADAPTER_STATUS_INVALID, SYNC_ADAPTER_STATUS_READY, SyncAdapter
from yaffo.logging_config import get_logger
from yaffo.sync.adapter_static import CURRENT_CONTRACT, analyze_adapter
from yaffo.sync.schemas import AdapterVersion, WebGrant

logger = get_logger(__name__)

PRESETS_DIR = Path(__file__).resolve().parent / "presets"


@dataclass(frozen=True)
class PresetFile:
    slug: str
    name: str
    filename: str
    version: str


PRESETS: tuple[PresetFile, ...] = (
    PresetFile(slug="preset.onedrive_source", name="OneDrive", filename="onedrive_source.star", version="2"),
    PresetFile(slug="preset.google_photos_target", name="Google Photos", filename="google_photos_target.star", version="1"),
    PresetFile(slug="preset.onedrive_target", name="OneDrive", filename="onedrive_target.star", version="1"),
)


def _presets_dir() -> Path:
    if PRESETS_DIR.is_dir():
        return PRESETS_DIR
    return BUNDLE_ROOT / "yaffo" / "sync" / "presets"


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def grants_from_manifest(manifest: list[dict], code_hash: str) -> list[dict]:
    granted_at = _now()
    return [WebGrant(method=entry["method"], url_pattern=entry["url_pattern"], grant_kind=entry["grant_kind"],
                     followup_of=None, risk=entry["risk"], granted_code_hash=code_hash,
                     granted_at=granted_at).to_dict()
            for entry in manifest]


def ensure_presets(session: Session) -> None:
    """Insert or update every shipped preset. Commits only when something changed."""
    rows = {row.slug: row for row in session.query(SyncAdapter).filter(
        SyncAdapter.slug.in_([p.slug for p in PRESETS])).all()}
    changed = False
    for preset in PRESETS:
        row = rows.get(preset.slug)
        try:
            code = (_presets_dir() / preset.filename).read_text(encoding="utf-8")
        except OSError as e:
            logger.error(f"preset {preset.slug} is missing from this build: {e}")
            continue
        analysis = analyze_adapter(code)
        if row is not None and row.preset_version == preset.version and row.published_code == code \
                and (row.published or {}).get("contract") == CURRENT_CONTRACT:
            continue
        if not analysis.ok:
            logger.error(f"preset {preset.slug} fails its static check: {analysis.problems}")
        metadata = analysis.metadata
        version = AdapterVersion(code_hash=analysis.code_hash, contract=CURRENT_CONTRACT, metadata=metadata,
                                 manifest=analysis.manifest).to_dict() if analysis.ok else None
        if row is None:
            row = SyncAdapter(slug=preset.slug, is_preset=True, grants=[], doc_domains=[])
            session.add(row)
        row.name = preset.name
        row.role = metadata.get("role", "source")
        row.provider = metadata.get("provider", "unknown")
        row.preset_version = preset.version
        row.handler = None
        row.working_code = row.published_code = code
        row.working = row.published = version
        row.grants = grants_from_manifest(analysis.manifest, analysis.code_hash) if analysis.ok else []
        row.status = SYNC_ADAPTER_STATUS_READY if analysis.ok else SYNC_ADAPTER_STATUS_INVALID
        changed = True
        logger.info(f"seeded preset adapter {preset.slug} v{preset.version}")
    if changed:
        session.commit()
