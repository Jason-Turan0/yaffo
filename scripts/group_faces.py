import numpy as np
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sklearn.cluster import DBSCAN
import pickle

from yaffo.common import DB_PATH
from yaffo.db import db
from yaffo.db.models import Face, Person, PersonFace  # adjust imports to your project
from yaffo.domain.compare_utils import load_embedding

# --- CONFIG ---
# ArcFace embeddings are L2-normalized, so we cluster by cosine distance (1 - cos).
# Genuine pairs sit well below this, impostors above; tune against the benchmark.
EPS = 0.5  # cosine-distance threshold (tune this!)
MIN_SAMPLES = 1  # how many faces needed to form a cluster

engine = create_engine(f"sqlite:///{DB_PATH}")
session = sessionmaker(bind=engine)()


def group_faces_and_create_people():
    # Step 1: load all faces + embeddings
    faces = session.query(Face).all()
    if not faces:
        print("No faces found in DB.")
        return

    person_delete = session.query(Person).delete()
    person_face_delete = session.query(PersonFace).delete()
    print(f"Deleted {person_face_delete} PersonFace and {person_delete} PersonFace")
    embeddings = []
    face_ids = []

    for face in faces:
        if face.embedding:
            emb = load_embedding(face.embedding)
            embeddings.append(emb)
            face_ids.append(face.id)

    embeddings = np.array(embeddings)

    # Step 2: cluster with DBSCAN
    clustering = DBSCAN(eps=EPS, min_samples=MIN_SAMPLES, metric="cosine").fit(embeddings)
    labels = clustering.labels_  # -1 means noise/unclustered

    # Step 3: create Person and link faces
    cluster_to_person = {}
    for face_id, label in zip(face_ids, labels):
        if label == -1:  # skip noise faces
            continue

        if label not in cluster_to_person:
            # Create new person
            person = Person(name=f"Person {label}")
            session.add(person)
            session.flush()  # assign id
            cluster_to_person[label] = person

        # Create PersonFace link
        person = cluster_to_person[label]
        link = PersonFace(person_id=person.id, face_id=face_id)
        session.add(link)

    session.commit()
    print(f"Created {len(cluster_to_person)} persons.")


if __name__ == "__main__":
    group_faces_and_create_people()
