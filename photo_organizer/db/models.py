from sqlalchemy import PrimaryKeyConstraint
from __main__ import db

class Photo(db.Model):
    __tablename__ = "photos"
    id = db.Column(db.Integer, primary_key=True)
    full_file_path = db.Column(db.String, unique=True)
    relative_file_path = db.Column(db.String, unique=True)
    hash = db.Column(db.String)
    date_taken = db.Column(db.String)
    faces = db.relationship(
        "Face",
        secondary="photo_faces",
        back_populates="photos"
    )

class Face(db.Model):
    __tablename__ = "faces"
    id = db.Column(db.Integer, primary_key=True)
    embedding = db.Column(db.LargeBinary)
    photo_id = db.Column(db.Integer, db.ForeignKey("photo.id"))
    # Relationships
    photos = db.relationship(
        "Photo",
        secondary="photo_faces",
        back_populates="faces"
    )
    people = db.relationship(
        "Person",
        secondary="people_face",
        back_populates="faces"
    )

class PhotoFace(db.Model):
    __tablename__ = "photo_faces"
    photo_id = db.Column(db.Integer, db.ForeignKey("photos.id"), primary_key=True)
    face_id = db.Column(db.Integer, db.ForeignKey("faces.id"), primary_key=True)
    __table_args__ = (
        PrimaryKeyConstraint("photo_id", "face_id"),
    )

class Person(db.Model):
    __tablename__ = "people"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String)
    # Relationship to faces through bridge table
    faces = db.relationship(
        "Face",
        secondary="people_face",
        back_populates="people"
    )

class PersonFace(db.Model):
    __tablename__ = "people_face"
    person_id = db.Column(db.Integer, db.ForeignKey("people.id"), primary_key=True)
    face_id = db.Column(db.Integer, db.ForeignKey("faces.id"), primary_key=True)
    __table_args__ = (
        PrimaryKeyConstraint("person_id", "face_id"),
    )
