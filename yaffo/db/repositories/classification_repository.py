"""Persistence for the photo-label vocabulary (`classification_labels`) and the
classifier's per-photo assignments (`photo_labels`). Used by the Settings admin
routes (vocabulary CRUD) and the classify-labels automation task (assignments)."""
from sqlalchemy.orm import Session

from yaffo.db.models import ClassificationLabel, PhotoLabel


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


def replace_photo_labels(session: Session, photo_id: int, assignments: list[tuple[int, float]]) -> None:
    """Set a photo's labels to exactly `assignments` (label_id, confidence) — wipe
    the prior rows first so re-classification is idempotent. Caller commits."""
    session.query(PhotoLabel).filter_by(photo_id=photo_id).delete(synchronize_session=False)
    for label_id, confidence in assignments:
        session.add(PhotoLabel(photo_id=photo_id, label_id=label_id, confidence=confidence))
