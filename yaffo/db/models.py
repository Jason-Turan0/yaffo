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


# PageVersion generation state machine (see docs/ai-page-builder-async-generation.md).
# A version is either "working" (IN_PROGRESS/READY/FAILED) or a committed snapshot
# (ACCEPTED); CANCELLED is transient — cancelled versions are deleted.
PAGE_VERSION_STATUS_IN_PROGRESS = "IN_PROGRESS"
PAGE_VERSION_STATUS_READY = "READY"
PAGE_VERSION_STATUS_FAILED = "FAILED"
PAGE_VERSION_STATUS_ACCEPTED = "ACCEPTED"
PAGE_VERSION_STATUS_CANCELLED = "CANCELLED"

# Conversation entry kinds. `user`/`assistant` are real chat turns (the only kinds
# fed back to the model); `status` is a tool-progress line ("Creating widget…") and
# `error` a failure line — both UI-only annotations persisted so the polled feed can
# replay the full back-and-forth, not just a live stream.
CONVERSATION_TYPE_USER = "user"
CONVERSATION_TYPE_ASSISTANT = "assistant"
CONVERSATION_TYPE_STATUS = "status"
CONVERSATION_TYPE_ERROR = "error"

# Kinds that are real conversational turns (rebuilt as model context on a follow-up
# / copied on fork); the rest are display-only annotations.
CONVERSATION_MODEL_TYPES = (CONVERSATION_TYPE_USER, CONVERSATION_TYPE_ASSISTANT)


class CustomPage(db.Model):
    __tablename__ = "custom_pages"

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String, nullable=False, default="Untitled Page")
    subtitle = db.Column(db.String, nullable=False, default="")
    show_title = db.Column(db.Boolean, nullable=False, default=True)
    # The live version shown in presentation, and the in-flight version a chat run
    # is generating into (or NULL — its presence is the UI-lock predicate). Plain
    # pointers into page_versions; the page <-> version relationship is circular, so
    # these use post_update to let the flush order them after the version exists.
    published_version_id = db.Column(db.Integer, db.ForeignKey("page_versions.id"), nullable=True)
    working_version_id = db.Column(db.Integer, db.ForeignKey("page_versions.id"), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    versions = db.relationship(
        "PageVersion",
        back_populates="page",
        cascade="all, delete-orphan",
        foreign_keys="PageVersion.page_id",
    )
    published_version = db.relationship(
        "PageVersion", foreign_keys=[published_version_id], post_update=True
    )
    working_version = db.relationship(
        "PageVersion", foreign_keys=[working_version_id], post_update=True
    )

    # Widgets and the conversation are version-scoped now; presentation/design read
    # through the published version so callers keep using page.widgets/page.messages.
    @property
    def widgets(self):
        return self.published_version.widgets if self.published_version else []

    @property
    def messages(self):
        return self.published_version.messages if self.published_version else []


class PageVersion(db.Model):
    __tablename__ = "page_versions"

    id = db.Column(db.Integer, primary_key=True)
    page_id = db.Column(db.Integer, db.ForeignKey("custom_pages.id", ondelete="CASCADE"), nullable=False)
    status = db.Column(db.String, nullable=False, default=PAGE_VERSION_STATUS_READY)
    # The version this was forked from — lineage for a possible future revert.
    parent_version_id = db.Column(db.Integer, db.ForeignKey("page_versions.id"), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    started_at = db.Column(db.DateTime, nullable=True)
    completed_at = db.Column(db.DateTime, nullable=True)
    error = db.Column(db.Text, nullable=True)

    page = db.relationship("CustomPage", back_populates="versions", foreign_keys=[page_id])
    widgets = db.relationship(
        "Widget",
        back_populates="version",
        cascade="all, delete-orphan",
        # No list order of its own — widgets are placed on a 2D grid; iterate in
        # reading order (top-to-bottom, left-to-right) for deterministic rendering.
        order_by="(Widget.grid_y, Widget.grid_x)",
    )
    messages = db.relationship(
        "Conversation",
        back_populates="version",
        cascade="all, delete-orphan",
        order_by="Conversation.id",
    )


class Widget(db.Model):
    __tablename__ = "widgets"

    # GUID minted server-side by the tool or client-side for manual adds, so a
    # draft's id is stable from creation through Save. Identity is per-version: a
    # fork copies a widget into a new version keeping its GUID, so the same GUID
    # recurs across versions (the same widget evolving) -- hence the composite PK.
    id = db.Column(db.String, primary_key=True)
    version_id = db.Column(
        db.Integer, db.ForeignKey("page_versions.id", ondelete="CASCADE"),
        primary_key=True, nullable=False,
    )
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

    version = db.relationship("PageVersion", back_populates="widgets")


class Conversation(db.Model):
    __tablename__ = "conversations"

    id = db.Column(db.Integer, primary_key=True)
    version_id = db.Column(db.Integer, db.ForeignKey("page_versions.id", ondelete="CASCADE"), nullable=False)
    # CONVERSATION_TYPE_*: user | assistant | status | error. `status`/`error` are
    # display-only; only user/assistant are replayed to the model.
    type = db.Column(db.String, nullable=False)
    content = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    version = db.relationship("PageVersion", back_populates="messages")