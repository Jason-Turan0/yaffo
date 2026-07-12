#!/usr/bin/env python3
"""One-off: rescore every assigned face against its person's current gallery.

PersonFace.similarity is a cache of "how much does this face look like this person",
and until now it was only ever written at assignment time — against the medoid gallery
as it stood at that moment, which the same assignment then rebuilt. So stored scores
were measured against galleries that no longer exist. Worse, a person's *first*
assignment had no gallery to score against at all and fell back to the mean of the
batch, scoring those faces against themselves.

That doesn't just misreport individual faces. The 0-100 scale the UI shows is
calibrated against the percentiles of these stored values (get_similarity_bounds), so
bad rows drag the band and skew the displayed score of every face, including the ones
that were scored correctly.

Assignments are untouched: this only rewrites the score column.

Run:  python -m scripts.recompute_person_similarities
"""
from yaffo.app import create_app
from yaffo.db import db
from yaffo.db.models import Person
from yaffo.db.repositories.person_repository import (
    get_similarity_bounds,
    recompute_person_similarities,
)


def recompute_all() -> None:
    app = create_app()
    with app.app_context():
        print(f"similarity band before: {get_similarity_bounds(db.session)}")

        people = db.session.query(Person).order_by(Person.name).all()
        total = 0
        for person in people:
            written = recompute_person_similarities(db.session, person.id)
            total += written
            if written:
                print(f"  {person.name}: rescored {written} face(s)")

        print(f"rescored {total} face(s) across {len(people)} person(s)")
        print(f"similarity band after:  {get_similarity_bounds(db.session)}")


if __name__ == "__main__":
    recompute_all()
