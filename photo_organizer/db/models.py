from sqlalchemy import PrimaryKeyConstraint
from photo_organizer.db import db
from datetime import datetime

class Photo(db.Model):
    __tablename__ = "photos"
    id = db.Column(db.Integer, primary_key=True)
    full_file_path = db.Column(db.String, unique=True)
    relative_file_path = db.Column(db.String, unique=True)
    hash = db.Column(db.String)
    date_taken = db.Column(db.String)
    latitude = db.Column(db.Float)
    longitude = db.Column(db.Float)
    location_name = db.Column(db.String)
    faces = db.relationship(
        "Face",
        back_populates="photo"
    )
    tags = db.relationship(
        "Tag",
        back_populates="photo",
        cascade="all, delete-orphan"
    )

class Tag(db.Model):
    __tablename__ = "tags"
    id = db.Column(db.Integer, primary_key=True)
    photo_id = db.Column(db.Integer, db.ForeignKey("photos.id", ondelete="CASCADE"), nullable=False)
    tag_name = db.Column(db.String, nullable=False)
    tag_value = db.Column(db.String)
    photo = db.relationship("Photo", back_populates="tags")

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
    # Face bounding box coordinates (from face_recognition)
    location_top = db.Column(db.Integer)
    location_right = db.Column(db.Integer)
    location_bottom = db.Column(db.Integer)
    location_left = db.Column(db.Integer)
    # Relationships
    # One-to-one with PersonFace
    person_face = db.relationship(
        "PersonFace",
        back_populates="face",
        uselist=False,
        cascade="all, delete-orphan"
    )
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
    person_faces = db.relationship("PersonFace", back_populates="person", cascade="all, delete-orphan")


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

    person_id = db.Column(db.Integer, db.ForeignKey("people.id"), nullable=False)
    face_id = db.Column(db.Integer, db.ForeignKey("faces.id"),  primary_key=True, unique=True, nullable=False)

    similarity = db.Column(db.Float)

    face = db.relationship("Face", back_populates="person_face", uselist=False, overlaps="people")
    person = db.relationship("Person", back_populates="person_faces", overlaps="faces,people")


JOB_STATUS_PENDING = "PENDING"
JOB_STATUS_RUNNING = "RUNNING"
JOB_STATUS_COMPLETED = "COMPLETED"
JOB_STATUS_CANCELLED = "CANCELLED"
JOB_STATUS_FAILED = "FAILED"


class Job(db.Model):
    __tablename__ = "jobs"

    id = db.Column(db.String, primary_key=True)
    name = db.Column(db.String, nullable=False)
    status = db.Column(db.String, nullable=False, default=JOB_STATUS_PENDING)

    task_count = db.Column(db.Integer, default=0)
    completed_count = db.Column(db.Integer, default=0)
    cancelled_count = db.Column(db.Integer, default=0)
    error_count = db.Column(db.Integer, default=0)

    started_at = db.Column(db.DateTime)
    completed_at = db.Column(db.DateTime)

    error = db.Column(db.Text)
    message = db.Column(db.Text)
    job_data = db.Column(db.Text)
    results = db.relationship("JobResult", back_populates="job")

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        progress = 0
        if self.task_count > 0:
            progress = int((self.completed_count / self.task_count) * 100)

        return {
            'id': self.id,
            'name': self.name,
            'status': self.status,
            'task_count': self.task_count,
            'completed_count': self.completed_count,
            'cancelled_count': self.cancelled_count,
            'error_count': self.error_count,
            'progress': progress,
            'started_at': self.started_at.isoformat() if self.started_at else None,
            'completed_at': self.completed_at.isoformat() if self.completed_at else None,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'error': self.error,
            'message': self.message,
        }

class JobResult(db.Model):
    __tablename__ = "job_results"

    id = db.Column(db.Integer, primary_key=True)
    job_id = db.Column(db.String, db.ForeignKey("jobs.id"), nullable=False)
    huey_task_id = db.Column(db.String, nullable=False, unique=True)
    result_data = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    job = db.relationship("Job", back_populates="results")