from sqlalchemy import PrimaryKeyConstraint
from photo_organizer.db import db

class Photo(db.Model):
    __tablename__ = "photos"
    id = db.Column(db.Integer, primary_key=True)
    full_file_path = db.Column(db.String, unique=True)
    relative_file_path = db.Column(db.String, unique=True)
    hash = db.Column(db.String)
    date_taken = db.Column(db.String)
    faces = db.relationship(
        "Face",
        back_populates="photo"
    )

FACE_STATUS_UNASSIGNED = "UNASSIGNED"
FACE_STATUS_ASSIGNED = "ASSIGNED"
FACE_STATUS_IGNORED = "IGNORED"

class Face(db.Model):
    __tablename__ = "faces"
    id = db.Column(db.Integer, primary_key=True)
    embedding = db.Column(db.LargeBinary)
    full_file_path = db.Column(db.String, unique=True)
    relative_file_path = db.Column(db.String, unique=True)
    photo_id = db.Column(db.Integer, db.ForeignKey("photos.id"))
    status = db.Column(db.String)
    # Relationships
    photo = db.relationship("Photo", back_populates="faces")
    people = db.relationship(
        "Person",
        secondary="people_face",
        back_populates="faces"
    )

class Person(db.Model):
    __tablename__ = "people"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String)
    avg_embedding = db.Column(db.LargeBinary)
    # Relationship to faces through bridge table
    faces = db.relationship(
        "Face",
        secondary="people_face",
        back_populates="people"
    )
    embeddings_by_year = db.relationship(
        "PersonEmbedding",
        back_populates="person",
        cascade="all, delete-orphan"
    )

class PersonEmbedding(db.Model):
    __tablename__ = "people_embeddings"
    person_id = db.Column(db.Integer, db.ForeignKey("people.id"), primary_key=True)
    year = db.Column(db.Integer, primary_key=True)
    included_face_ids = db.Column(db.Text)
    avg_embedding = db.Column(db.LargeBinary)
    person = db.relationship(
        "Person",
        back_populates="embeddings_by_year"
    )
    __table_args__ = (
        PrimaryKeyConstraint("person_id", "year"),
    )

class PersonFace(db.Model):
    __tablename__ = "people_face"
    person_id = db.Column(db.Integer, db.ForeignKey("people.id"), primary_key=True)
    face_id = db.Column(db.Integer, db.ForeignKey("faces.id"), primary_key=True)
    similarity = db.Column(db.Float)
    __table_args__ = (
        PrimaryKeyConstraint("person_id", "face_id"),
    )
