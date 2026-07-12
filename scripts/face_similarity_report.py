#!/usr/bin/env python3
"""Explain one face's similarity to one person: its score against every life-stage
medoid, and which of those the app actually uses.

Scoring is per-stage (compare_utils.reference_embedding_for_face): a face is measured
against the medoid of the stage it belongs to, falling back to the person's overall
medoid for a stage they have no faces in. That makes a low score ambiguous from the
outside -- is the face a poor match, or is it being bucketed into the wrong stage? This
prints the whole picture so you can tell the two apart: the face's stage and where it
came from, its cosine against *each* stage, and which one was chosen.

Run:  python -m scripts.face_similarity_report --face 295 --person Obama
      python -m scripts.face_similarity_report            (prompts for both)
"""
import argparse
from pathlib import Path
from typing import Optional

import numpy as np

from yaffo.app import create_app
from yaffo.common import APP_NAME
from yaffo.db import db
from yaffo.db.models import Face, Person, PersonFace
from yaffo.db.repositories.person_repository import get_similarity_bounds
from yaffo.domain.compare_utils import (
    load_embedding,
    similarity_to_ui_percent,
)
from yaffo.domain.life_stages import effective_birthdate, life_stage, life_stage_for_year
YAFFO_DATA_DIR = Path.home() / "Pictures" / f"{APP_NAME}.db"

def _resolve_person(value: str) -> Optional[Person]:
    """By id if it looks like one, else by name (case-insensitive)."""
    value = value.strip()
    if value.isdigit():
        person = db.session.get(Person, int(value))
        if person:
            return person
    return (
        db.session.query(Person)
        .filter(db.func.lower(Person.name) == value.lower())
        .first()
    )


def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    # Embeddings are L2-normalized, so the dot product is the cosine.
    return float(np.dot(a, b))


def report(face_id: int, person_key: str) -> None:
    face = db.session.get(Face, face_id)
    if face is None:
        print(f"No face with id {face_id}.")
        return
    if not face.embedding:
        print(f"Face {face_id} has no embedding — nothing to score.")
        return

    person = _resolve_person(person_key)
    if person is None:
        names = [name for (name,) in db.session.query(Person.name).order_by(Person.name)]
        print(f"No person matching {person_key!r}. Known people: {', '.join(names) or '(none)'}")
        return

    face_emb = load_embedding(face.embedding)
    year = face.media_item.year if face.media_item else None
    birthdate = effective_birthdate(person)
    stage = life_stage(birthdate, year, face.estimated_age)
    # Which input decided the stage: the birthdate+year path, or the age fallback?
    from_year = life_stage_for_year(birthdate, year)
    stage_source = (
        f"birthdate {birthdate} + photo year {year}" if from_year != "unknown"
        else f"predicted age {face.estimated_age} (no birthdate or no photo year)"
    )

    print(f"\nFace {face.id}")
    print(f"  photo        {face.media_item.full_file_path if face.media_item else '(none)'}")
    print(f"  photo year   {year}")
    print(f"  predicted age {face.estimated_age}")
    print(f"  det score    {face.det_score}")

    print(f"\nPerson {person.id}: {person.name}")
    print(f"  birthdate    {person.birthdate} (actual) / {person.estimated_birthdate} (estimated)"
          f" -> using {birthdate}")

    print(f"\nLife stage of this face: {stage}   [from {stage_source}]")

    stage_medoids = {
        s.life_stage: load_embedding(s.avg_embedding)
        for s in person.stage_embeddings
        if s.avg_embedding
    }
    overall = load_embedding(person.avg_embedding) if person.avg_embedding else None
    if not stage_medoids and overall is None:
        print("\nThis person has no gallery at all — the face cannot be scored (NULL).")
        return

    # The medoid the app scores against: this face's own stage, else the overall one.
    used_stage = stage if stage in stage_medoids else "(overall)"

    floor, ceil = get_similarity_bounds(db.session)
    print(f"\nScore against each life-stage medoid   [UI band: floor={floor:.3f} ceil={ceil:.3f}]")
    print(f"  {'stage':<12} {'cosine':>8} {'UI %':>6}   used")
    rows = [(s, medoid) for s, medoid in sorted(stage_medoids.items())]
    if overall is not None:
        rows.append(("(overall)", overall))
    for stage_name, medoid in rows:
        score = _cosine(face_emb, medoid)
        marker = "  <-- used" if stage_name == used_stage else ""
        print(f"  {stage_name:<12} {score:>8.4f} {similarity_to_ui_percent(score, floor, ceil):>5}%{marker}")

    reference = stage_medoids.get(stage, overall)
    score = _cosine(face_emb, reference)
    print(f"\nScore the app uses: {score:.4f} ({similarity_to_ui_percent(score, floor, ceil)}%)"
          f" — against the {used_stage} medoid")
    if used_stage == "(overall)":
        print(f"  (this person has no '{stage}' medoid, so the overall one stands in)")

    best_stage, best = max(rows, key=lambda pair: _cosine(face_emb, pair[1]))
    if best_stage != used_stage:
        print(f"  Best-matching stage is '{best_stage}' at {_cosine(face_emb, best):.4f} — the face "
              f"resembles that stage more, but is being scored as '{stage}'.")

    stored = (
        db.session.query(PersonFace.similarity)
        .filter_by(person_id=person.id, face_id=face.id)
        .scalar()
    )
    if stored is None:
        assigned_to = (
            db.session.query(Person.name)
            .join(PersonFace, PersonFace.person_id == Person.id)
            .filter(PersonFace.face_id == face.id)
            .scalar()
        )
        print(f"\nStored score: none — this face is "
              f"{'assigned to ' + assigned_to if assigned_to else 'not assigned to anyone'}.")
    else:
        drift = abs(stored - score)
        print(f"\nStored score: {stored:.4f} (computed now: {score:.4f}, drift {drift:.4f})")
        if drift > 1e-4:
            print("  The cache is stale — run: python -m scripts.recompute_person_similarities")
    print()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--face", type=int, help="face id")
    parser.add_argument("--person", type=str, help="person id or name")
    args = parser.parse_args()

    app = create_app(db_path=YAFFO_DATA_DIR)
    with app.app_context():
        face_id = args.face if args.face is not None else int(input("Face id: ").strip())
        person_key = args.person if args.person else input("Person (id or name): ")
        report(face_id, person_key)


if __name__ == "__main__":
    main()
