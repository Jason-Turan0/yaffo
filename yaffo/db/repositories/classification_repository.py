"""Persistence for the photo-label vocabulary (`classification_labels`) and the
classifier's per-photo assignments (`photo_labels`). Used by the Settings admin
routes (vocabulary CRUD) and the classify-labels automation task (assignments)."""
from sqlalchemy import insert
from sqlalchemy.orm import Session

from yaffo.db.models import ClassificationLabel, PhotoLabel

# SQLite caps bound parameters per statement (~999); chunk the IN-list delete so a
# large re-classify batch never blows that limit.
_DELETE_CHUNK = 500


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


def set_enabled(session: Session, label_id: int, enabled: bool) -> None:
    label = session.get(ClassificationLabel, label_id)
    if label is not None:
        label.enabled = enabled
        session.commit()


def bulk_replace_photo_labels(
    session: Session, results: list[tuple[int, list[tuple[int, float]]]]
) -> None:
    """Set each photo's labels to exactly its assignments, for a batch of photos in
    one short transaction: wipe those photos' prior rows, then bulk-insert the new
    ones (one executemany). `results` is [(photo_id, [(label_id, confidence), ...])].
    A photo with an empty assignment list is still included, so its stale labels are
    cleared. Idempotent; commits once. Callers compute (e.g. CLIP inference) *before*
    calling this so no write lock is held during the slow work."""
    if not results:
        return
    photo_ids = [photo_id for photo_id, _ in results]
    for start in range(0, len(photo_ids), _DELETE_CHUNK):
        chunk = photo_ids[start:start + _DELETE_CHUNK]
        session.query(PhotoLabel).filter(PhotoLabel.photo_id.in_(chunk)).delete(synchronize_session=False)
    rows = [
        {"photo_id": photo_id, "label_id": label_id, "confidence": confidence}
        for photo_id, assignments in results
        for label_id, confidence in assignments
    ]
    if rows:
        session.execute(insert(PhotoLabel), rows)
    session.commit()
