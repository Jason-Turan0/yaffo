from sqlalchemy import PrimaryKeyConstraint
from yaffo.db import db
from datetime import datetime

PHOTO_STATUS_IMPORTED = "IMPORTED"
PHOTO_STATUS_INDEXED = "INDEXED"
PHOTO_STATUS_SYNCED = "SYNCED"

class Photo(db.Model):
    __tablename__ = "photos"
    id = db.Column(db.Integer, primary_key=True)
    full_file_path = db.Column(db.String, unique=True)
    date_taken = db.Column(db.String, nullable=True)
    year = db.Column(db.Integer, nullable=True)
    month = db.Column(db.Integer, nullable=True)
    latitude = db.Column(db.Float)
    longitude = db.Column(db.Float)
    location_name = db.Column(db.String)
    status = db.Column(db.String, default=PHOTO_STATUS_IMPORTED)
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
    results = db.relationship("JobResult", back_populates="job", cascade="all, delete-orphan")

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

    def to_dict_with_view_props(self, has_results: bool = False, results_route: str | None = None):
        """Convert job to dict with view-specific properties"""
        job_dict = self.to_dict()
        job_dict['has_results'] = has_results
        job_dict['results_route'] = results_route
        return job_dict

class JobResult(db.Model):
    __tablename__ = "job_results"

    id = db.Column(db.Integer, primary_key=True)
    job_id = db.Column(db.String, db.ForeignKey("jobs.id"), nullable=False)
    huey_task_id = db.Column(db.String, nullable=False, unique=True)
    result_data = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    job = db.relationship("Job", back_populates="results")

class ApplicationSettings(db.Model):
    __tablename__ = "application_settings"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String, unique=True, nullable=False)
    type = db.Column(db.String, nullable=False)
    value = db.Column(db.String)


class CustomPage(db.Model):
    __tablename__ = "custom_pages"

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String, nullable=False, default="Untitled Page")
    subtitle = db.Column(db.String, nullable=False, default="")
    show_title = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    widgets = db.relationship(
        "Widget",
        back_populates="page",
        cascade="all, delete-orphan",
        # No list order of its own — widgets are placed on a 2D grid; iterate in
        # reading order (top-to-bottom, left-to-right) for deterministic rendering.
        order_by="(Widget.grid_y, Widget.grid_x)",
    )
    messages = db.relationship(
        "Conversation",
        back_populates="page",
        cascade="all, delete-orphan",
        order_by="Conversation.id",
    )


class Widget(db.Model):
    __tablename__ = "widgets"

    # GUID minted server-side by the tool or client-side for manual adds, so a
    # draft's id is stable from creation through Save.
    id = db.Column(db.String, primary_key=True)
    page_id = db.Column(db.Integer, db.ForeignKey("custom_pages.id", ondelete="CASCADE"), nullable=False)
    title = db.Column(db.String, default="Untitled widget")
    data_query = db.Column(db.JSON, default=dict)  # named queries (author / AI-defined)
    state = db.Column(db.JSON, default=dict)  # widget-owned persisted UI state
    html = db.Column(db.Text, default="")
    css = db.Column(db.Text, default="")
    js = db.Column(db.Text, default="")
    grid_x = db.Column(db.Integer, default=0)
    grid_y = db.Column(db.Integer, default=0)
    grid_w = db.Column(db.Integer, default=4)
    grid_h = db.Column(db.Integer, default=3)

    page = db.relationship("CustomPage", back_populates="widgets")


class Conversation(db.Model):
    __tablename__ = "conversations"

    id = db.Column(db.Integer, primary_key=True)
    page_id = db.Column(db.Integer, db.ForeignKey("custom_pages.id", ondelete="CASCADE"), nullable=False)
    role = db.Column(db.String, nullable=False)  # user | assistant
    content = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    page = db.relationship("CustomPage", back_populates="messages")