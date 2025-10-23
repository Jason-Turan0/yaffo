import numpy as np
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sklearn.cluster import DBSCAN
import pickle

from photo_organizer.common import DB_PATH
from photo_organizer.db import db
from photo_organizer.db.models import Face, Person, PersonFace  # adjust imports to your project

# --- CONFIG ---
EPS = 0.45  # distance threshold (tune this!)
MIN_SAMPLES = 1  # how many faces needed to form a cluster

engine = create_engine(f"sqlite:///{DB_PATH}")
session = sessionmaker(bind=engine)()

def load_embedding(blob: bytes) -> np.ndarray:
    arr = np.frombuffer(blob, dtype=np.float64)
    return arr.reshape((128,))


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
    clustering = DBSCAN(eps=EPS, min_samples=MIN_SAMPLES, metric="euclidean").fit(embeddings)
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
