"""Persistence for the photo-label vocabulary (`classification_labels`) and the
classifier's per-photo assignments (`photo_labels`). Used by the Settings admin
routes (vocabulary CRUD) and the classify-labels automation task (assignments)."""
from collections.abc import Sequence
from typing import NamedTuple

from sqlalchemy import insert
from sqlalchemy.orm import Session

from yaffo.db.models import ClassificationLabel, MediaLabel

# SQLite caps bound parameters per statement (~999); chunk the IN-list delete so a
# large re-classify batch never blows that limit.
_DELETE_CHUNK = 500


class MediaLabelAssignment(NamedTuple):
    media_item_id: int
    assignments: list[tuple[int, float]]


def list_labels(session: Session) -> list[ClassificationLabel]:
    """The whole vocabulary, defaults first then alphabetical — the admin view."""
    return (
        session.query(ClassificationLabel)
        .order_by(ClassificationLabel.is_default.desc(), ClassificationLabel.name)
        .all()
    )


def get_enabled_labels(session: Session) -> list[ClassificationLabel]:
    return (
        session.query(ClassificationLabel)
        .filter(ClassificationLabel.enabled.is_(True))
        .order_by(ClassificationLabel.name)
        .all()
    )


def get_label_by_name(session: Session, name: str) -> ClassificationLabel | None:
    return session.query(ClassificationLabel).filter_by(name=name).first()


def create_label(session: Session, name: str, prompt: str | None = None) -> ClassificationLabel:
    label = ClassificationLabel(name=name, prompt=prompt or None, is_default=False)
    session.add(label)
    session.commit()
    return label


def delete_label(session: Session, label_id: int) -> None:
    label = session.get(ClassificationLabel, label_id)
    if label is not None:
        session.delete(label)
        session.commit()


def label_use_count(session: Session, label_id: int) -> int:
    """How many photos the classifier has given this label."""
    return session.query(MediaLabel).filter_by(label_id=label_id).count()


def set_enabled(session: Session, label_id: int, enabled: bool) -> None:
    label = session.get(ClassificationLabel, label_id)
    if label is not None:
        label.enabled = enabled
        session.commit()


def bulk_replace_media_labels(
    session: Session, results: Sequence[MediaLabelAssignment]
) -> list[int]:
    """Set each photo's labels to exactly its assignments, for a batch of photos in
    one short transaction: wipe those photos' prior rows, then bulk-insert the new
    ones (one executemany). Each result names a media item and its (label_id, confidence)
    assignments.
    A photo with an empty assignment list is still included, so its stale labels are
    cleared. Unchanged assignments keep their existing rows. Returns IDs whose
    assignments changed; commits once when there are changes. Callers compute
    (e.g. CLIP inference) *before* calling this so no write lock is held during
    the slow work."""
    if not results:
        return []
    media_item_ids = [result.media_item_id for result in results]
    existing: dict[int, set[tuple[int, float]]] = {}
    for start in range(0, len(media_item_ids), _DELETE_CHUNK):
        chunk = media_item_ids[start:start + _DELETE_CHUNK]
        for media_item_id, label_id, confidence in session.query(
            MediaLabel.media_item_id, MediaLabel.label_id, MediaLabel.confidence
        ).filter(MediaLabel.media_item_id.in_(chunk)).all():
            existing.setdefault(media_item_id, set()).add((label_id, confidence))
    changed = [
        result for result in results
        if existing.get(result.media_item_id, set()) != set(result.assignments)
    ]
    if not changed:
        return []
    media_item_ids = [result.media_item_id for result in changed]
    for start in range(0, len(media_item_ids), _DELETE_CHUNK):
        chunk = media_item_ids[start:start + _DELETE_CHUNK]
        session.query(MediaLabel).filter(MediaLabel.media_item_id.in_(chunk)).delete(synchronize_session=False)
    rows = [
        {"media_item_id": result.media_item_id, "label_id": label_id, "confidence": confidence}
        for result in changed
        for label_id, confidence in result.assignments
    ]
    if rows:
        session.execute(insert(MediaLabel), rows)
    session.commit()
    return media_item_ids
