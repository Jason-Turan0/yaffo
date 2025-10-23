from pathlib import Path

import face_recognition
from sqlalchemy import create_engine, insert
from sqlalchemy.orm import sessionmaker, joinedload
from tqdm import tqdm
from yaffo.common import DB_PATH
from yaffo.db.models import Face, Person, PersonFace, FACE_STATUS_UNASSIGNED, FACE_STATUS_IGNORED, \
    FACE_STATUS_ASSIGNED
from yaffo.db.repositories.person_repository import update_person_embedding
from yaffo.domain.compare_utils import calculate_face_similarity, load_embedding
from concurrent.futures import ProcessPoolExecutor, as_completed

from yaffo.scripts.index_photos import load_image_file

engine = create_engine(f"sqlite:///{DB_PATH}")
session = sessionmaker(bind=engine)()
THRESHOLD = 0.96  # configurable similarity threshold
IGNORE_THRESHOLD = 0.92  # configurable similarity threshold
max_workers = 8
batch_size = 50

def find_matching_person(unassigned_face: Face, people: list[Person]) -> tuple[int,float] | str | None:
    try:
        face_similarity = calculate_face_similarity(unassigned_face, people)
        matching_people = [person_id for (person_id, similarity) in face_similarity.items() if similarity > THRESHOLD]
        above_ignore_threshold = [person_id for (person_id, similarity) in face_similarity.items() if similarity > IGNORE_THRESHOLD]
        if len(matching_people) == 1:
            return matching_people[0], face_similarity.get(matching_people[0])
        if len(above_ignore_threshold) == 0:
            return FACE_STATUS_IGNORED
    except:
        print("Skipping face due to error:", unassigned_face.id)
    return None

def assign_faces():
    unassigned_faces: list[Face] = (session
             .query(Face)
             .filter(Face.status == FACE_STATUS_UNASSIGNED).all())

    people: list[Person] = (session.query(Person)
              .options(joinedload(Person.embeddings_by_year))
              .order_by(Person.name)
              .all()
              )
    print("Number of unassigned faces:", len(unassigned_faces))
    assigned_face_count = 0
    skipped_face_count = 0
    #face_id, person_id, similarity
    updates: list[tuple[int, int, float]] = []
    ignored_face_ids: list[int] = []

    futures = []
    with ProcessPoolExecutor(max_workers=max_workers) as executor:
        for unassigned_face in unassigned_faces:
            futures.append(executor.submit(find_matching_person, unassigned_face, people))

        for i, future in enumerate(tqdm(as_completed(futures),  total=len(unassigned_faces), desc="Calculating similarity", unit="face")):
            unassigned_face = unassigned_faces[i]
            result: tuple[int, float] | str | None = future.result()
            if not result:
                skipped_face_count += 1
            elif result == FACE_STATUS_IGNORED:
                ignored_face_ids.append(unassigned_face.id)
            else:
                updates.append((unassigned_face.id, result[0], result[1]))
                assigned_face_count += 1

    print("Update count:", assigned_face_count)
    print("Skipped count:", skipped_face_count)
    print("Ignored count:", len(ignored_face_ids))

    stmt = insert(PersonFace).values([
        {"person_id": person_id, "face_id": face_id, "similarity": similarity}
        for face_id, person_id, similarity in updates
    ])
    session.execute(stmt)
    assigned_face_ids : set[int] = set([tuple[0] for tuple in updates])
    session.query(Face).filter(Face.id.in_(assigned_face_ids)).update(
        {Face.status: FACE_STATUS_ASSIGNED}, synchronize_session=False
    )
    session.query(Face).filter(Face.id.in_(ignored_face_ids)).update(
        {Face.status: FACE_STATUS_IGNORED}, synchronize_session=False
    )

    session.commit()

def recalculate_face_embedding():
    faces: list[Face] = (session.query(Face)
                            .all()
                            )
    for face in faces:
        try:
            load_embedding(face.embedding)
        except:
            print("Fixing face due to error:", face.id)
            image = load_image_file(Path(face.full_file_path))
            face_embeddings = face_recognition.face_encodings(image)

            if len(face_embeddings) == 1:
                face.embedding = face_embeddings.tobytes()
            else:
                print("Deleting face due to error:", face.id)
                session.query(PersonFace).filter(PersonFace.face_id == face.id).delete()
                session.query(Face).filter(Face.id == face.id).delete()

            session.commit()

def recalculate_person_embedding():
    people: list[Person] = (session.query(Person)
                            .options(joinedload(Person.embeddings_by_year))
                            .order_by(Person.name)
                            .all()
                            )
    for person in people:
        update_person_embedding(person.id, session)
    print(f"Finished calculating person embedding for {len(people)} people")

if __name__ == "__main__":
    recalculate_person_embedding()