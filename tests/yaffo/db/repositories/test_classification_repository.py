"""Repository tests for classification labels — focused on bulk_replace_photo_labels,
which the classify-labels job uses to flush a whole batch of assignments in one
short, idempotent transaction (delete the photos' prior labels, then bulk insert)."""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from yaffo.db import db
from yaffo.db.models import ClassificationLabel, Photo, PhotoLabel
from yaffo.db.repositories import classification_repository as repo

pytestmark = pytest.mark.unit


@pytest.fixture
def session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    db.metadata.create_all(engine)
    with Session(engine) as sess:
        yield sess
    engine.dispose()


@pytest.fixture
def seeded(session):
    """Two photos and two labels; returns (photo_ids, label_ids)."""
    photos = [Photo(full_file_path=f"/p/{i}.jpg") for i in range(2)]
    labels = [ClassificationLabel(name=n) for n in ("dog", "beach")]
    session.add_all(photos + labels)
    session.commit()
    return [p.id for p in photos], [label.id for label in labels]


def _labels_for(session, photo_id):
    rows = session.query(PhotoLabel).filter_by(photo_id=photo_id).all()
    return {(r.label_id, r.confidence) for r in rows}


def test_bulk_insert_assigns_each_photo(session, seeded):
    (p0, p1), (dog, beach) = seeded
    repo.bulk_replace_photo_labels(session, [
        (p0, [(dog, 0.9), (beach, 0.4)]),
        (p1, [(beach, 0.8)]),
    ])
    assert _labels_for(session, p0) == {(dog, 0.9), (beach, 0.4)}
    assert _labels_for(session, p1) == {(beach, 0.8)}


def test_bulk_replace_wipes_prior_labels(session, seeded):
    (p0, _), (dog, beach) = seeded
    repo.bulk_replace_photo_labels(session, [(p0, [(dog, 0.9)])])
    # Re-running with a different set replaces, not appends.
    repo.bulk_replace_photo_labels(session, [(p0, [(beach, 0.5)])])
    assert _labels_for(session, p0) == {(beach, 0.5)}


def test_empty_assignments_clears_photo(session, seeded):
    (p0, _), (dog, _) = seeded
    repo.bulk_replace_photo_labels(session, [(p0, [(dog, 0.9)])])
    repo.bulk_replace_photo_labels(session, [(p0, [])])  # still listed -> stale labels cleared
    assert _labels_for(session, p0) == set()


def test_empty_results_is_noop(session, seeded):
    (p0, _), (dog, _) = seeded
    repo.bulk_replace_photo_labels(session, [(p0, [(dog, 0.9)])])
    repo.bulk_replace_photo_labels(session, [])  # untouched photos keep their labels
    assert _labels_for(session, p0) == {(dog, 0.9)}
