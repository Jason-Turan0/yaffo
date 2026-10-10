"""Resolve OneDrive export layout into the target stored on each ledger row.

The adapter always receives a destination relative to the chosen OneDrive folder.
Doing this before the diff makes layout changes visible as removals and additions,
and lets reconciliation recover pending uploads from the stored target alone.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from yaffo.db.models import MediaItem
from yaffo.sync.folder_target import LAYOUT_BY_DATE, LAYOUT_FLAT, LAYOUT_PER_TARGET, UNDATED_FOLDER
from yaffo.sync.schemas import SELECTION_SCRIPT, Selection
from yaffo.sync.selection import Desired
from yaffo.utils.photo_dates import parse_date_taken


def one_drive_targets(session: Session, desired: list[Desired], selection: Selection,
                      layout: str) -> list[Desired]:
    """Apply the account target's layout; custom rules keep their own routing."""
    if selection.kind == SELECTION_SCRIPT or layout == LAYOUT_PER_TARGET:
        return desired
    if layout == LAYOUT_FLAT:
        return [Desired(item.media_item_id, ".", item.caption) for item in desired]
    if layout != LAYOUT_BY_DATE:
        raise ValueError(f"unknown OneDrive layout {layout!r}")
    dates = dict(session.query(MediaItem.id, MediaItem.date_taken).filter(
        MediaItem.id.in_([item.media_item_id for item in desired])).all())
    routed = []
    for item in desired:
        try:
            taken = parse_date_taken(dates.get(item.media_item_id))
        except (TypeError, ValueError):
            taken = None
        folder = f"{taken.year:04d}/{taken.month:02d}" if taken else UNDATED_FOLDER
        routed.append(Desired(item.media_item_id, folder, item.caption))
    return routed
