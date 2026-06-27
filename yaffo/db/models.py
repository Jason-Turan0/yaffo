from flask_babel import lazy_gettext
from sqlalchemy import PrimaryKeyConstraint
from yaffo.db import db
from yaffo.utils.time import utcnow
# Re-exported from common (the leaf that owns them) so existing
# `from yaffo.db.models import MEDIA_TYPE_*` imports keep working.
from yaffo.common import MEDIA_TYPE_PHOTO, MEDIA_TYPE_VIDEO

MEDIA_STATUS_IMPORTED = "IMPORTED"
MEDIA_STATUS_INDEXED = "INDEXED"
MEDIA_STATUS_SYNCED = "SYNCED"

class MediaItem(db.Model):
    __tablename__ = "media_items"
    id = db.Column(db.Integer, primary_key=True)
    full_file_path = db.Column(db.String, unique=True)
    # Discriminates a photo row from a video row. Every shared column below
    # (date_taken, location, faces, tags, labels, favorite, status) applies to
    # both; the video-only columns that follow are NULL for photos.
    media_type = db.Column(db.String, nullable=False, default=MEDIA_TYPE_PHOTO)
    # Video-only metadata (see docs/development/video.md). NULL on photo rows.
    poster_path = db.Column(db.String, nullable=True)
    duration_seconds = db.Column(db.Float, nullable=True)
    width = db.Column(db.Integer, nullable=True)
    height = db.Column(db.Integer, nullable=True)
    video_codec = db.Column(db.String, nullable=True)
    # The camera's *local wall-clock* capture time (EXIF DateTimeOriginal), stored
    # naive ISO-8601 — never UTC. EXIF carries no timezone, so there is no UTC
    # instant to recover; converting only some photos (e.g. phones that record an
    # offset) would mix clocks and break time comparisons like geotag_from_neighbors.
    # Parse it back through utils.photo_dates.parse_date_taken (asserts naive).
    date_taken = db.Column(db.String, nullable=True)
    year = db.Column(db.Integer, nullable=True)
    month = db.Column(db.Integer, nullable=True)
    latitude = db.Column(db.Float)
    longitude = db.Column(db.Float)
    location_name = db.Column(db.String)
    # Capture device, derived from EXIF Make/Model at index time (e.g. "FUJIFILM
    # X-T200", "Apple iPhone 6"). The rest of the EXIF metadata is intentionally
    # not persisted; see utils.index_photos.device_from_exif.
    device = db.Column(db.String, nullable=True)
    # Tri-state favorite: NULL = never set (the default; exports/treat as nothing),
    # True = favorited. Toggled from the photo view; exported as a keyword only when set.
    favorite = db.Column(db.Boolean, nullable=True)
    status = db.Column(db.String, default=MEDIA_STATUS_IMPORTED)
    faces = db.relationship(
        "Face",
        back_populates="media_item"
    )
    tags = db.relationship(
        "Tag",
        back_populates="media_item",
        cascade="all, delete-orphan"
    )
    labels = db.relationship(
        "MediaLabel",
        back_populates="media_item",
        cascade="all, delete-orphan",
        order_by="MediaLabel.confidence.desc()",
    )

class Tag(db.Model):
    __tablename__ = "tags"
    id = db.Column(db.Integer, primary_key=True)
    media_item_id = db.Column(db.Integer, db.ForeignKey("media_items.id", ondelete="CASCADE"), nullable=False)
    tag_name = db.Column(db.String, nullable=False)
    tag_value = db.Column(db.String)
    media_item = db.relationship("MediaItem", back_populates="tags")


class ClassificationLabel(db.Model):
    """An entry in the user-curated vocabulary the classify-labels automation scores
    photos against (zero-shot CLIP). `prompt` overrides the embedded text when set;
    otherwise the default "a photo of <name>" template is used (see effective_prompt).
    `is_default` marks the rows seeded with a fresh install (vs. user-added)."""
    __tablename__ = "classification_labels"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String, unique=True, nullable=False)
    prompt = db.Column(db.String)
    enabled = db.Column(db.Boolean, nullable=False, default=True)
    is_default = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, default=utcnow)

    @property
    def effective_prompt(self) -> str:
        return (self.prompt or "").strip() or f"a photo of {self.name}"


class MediaLabel(db.Model):
    """A label the classifier assigned to a media item, with its CLIP cosine
    confidence. Distinct from Tag (manual, freeform k/v): these are auto-generated,
    scored, and replaced wholesale on each re-classification of a media item."""
    __tablename__ = "media_labels"
    media_item_id = db.Column(db.Integer, db.ForeignKey("media_items.id", ondelete="CASCADE"), nullable=False)
    label_id = db.Column(db.Integer, db.ForeignKey("classification_labels.id", ondelete="CASCADE"), nullable=False)
    confidence = db.Column(db.Float)
    __table_args__ = (PrimaryKeyConstraint("media_item_id", "label_id"),)
    media_item = db.relationship("MediaItem", back_populates="labels")
    label = db.relationship("ClassificationLabel")

FACE_STATUS_UNASSIGNED = "UNASSIGNED"
FACE_STATUS_PROCESSING = "PROCESSING"
FACE_STATUS_ASSIGNED = "ASSIGNED"
FACE_STATUS_IGNORED = "IGNORED"

class Face(db.Model):
    __tablename__ = "faces"
    id = db.Column(db.Integer, primary_key=True)
    embedding = db.Column(db.LargeBinary)
    full_file_path = db.Column(db.String, unique=True)
    media_item_id = db.Column(db.Integer, db.ForeignKey("media_items.id"))
    status = db.Column(db.String)
    # Predicted age at the time the photo was taken (InsightFace genderage); used,
    # aggregated over a person's faces, to estimate their birthdate.
    estimated_age = db.Column(db.Float)
    # InsightFace genderage prediction: 0 = female, 1 = male.
    gender = db.Column(db.Integer)
    # SCRFD detection confidence (0-1); low values flag blurry/occluded/false faces.
    det_score = db.Column(db.Float)
    # Face bounding box coordinates (top/right/bottom/left, from face detection)
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
    media_item = db.relationship("MediaItem", back_populates="faces")
    people = db.relationship(
        "Person",
        secondary="people_face",
        back_populates="faces",
        overlaps="person_face,person,face,person_faces",
    )

class Person(db.Model):
    __tablename__ = "people"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String)
    # User-specified gender: 0 = female, 1 = male. When NULL, filters fall back
    # to the InsightFace estimate stored on each associated Face.
    gender = db.Column(db.Integer, nullable=True)
    avg_embedding = db.Column(db.LargeBinary)
    # User-entered actual birthdate (wins for life-stage bucketing) and the
    # birthdate estimated from the person's faces' predicted ages.
    birthdate = db.Column(db.Date)
    estimated_birthdate = db.Column(db.Date)
    # Relationship to faces through bridge table
    faces = db.relationship(
        "Face",
        secondary="people_face",
        back_populates="people",
        overlaps="person_face,person,face,person_faces",
    )
    # One representative (medoid) embedding per life stage (baby/child/teen/...).
    stage_embeddings = db.relationship(
        "PersonEmbedding",
        back_populates="person",
        cascade="all, delete-orphan"
    )
    person_faces = db.relationship(
        "PersonFace", back_populates="person", cascade="all, delete-orphan", overlaps="faces,people"
    )


class PersonEmbedding(db.Model):
    __tablename__ = "people_embeddings"
    person_id = db.Column(db.Integer, db.ForeignKey("people.id"), primary_key=True)
    life_stage = db.Column(db.String, primary_key=True)
    included_face_ids = db.Column(db.Text)
    avg_embedding = db.Column(db.LargeBinary)
    person = db.relationship(
        "Person",
        back_populates="stage_embeddings"
    )
    __table_args__ = (
        PrimaryKeyConstraint("person_id", "life_stage"),
    )

class PersonFace(db.Model):
    __tablename__ = "people_face"

    person_id = db.Column(db.Integer, db.ForeignKey("people.id"), nullable=False)
    face_id = db.Column(db.Integer, db.ForeignKey("faces.id"),  primary_key=True, unique=True, nullable=False)

    similarity = db.Column(db.Float)

    face = db.relationship("Face", back_populates="person_face", uselist=False, overlaps="faces,people")
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

    # Set when this job is a run of an automation; NULL for user-initiated jobs.
    # Reuses the job machinery as the automation's run state/history.
    automation_id = db.Column(
        db.Integer, db.ForeignKey("automations.id", ondelete="SET NULL"), nullable=True
    )

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
    automation = db.relationship("Automation", back_populates="jobs")

    created_at = db.Column(db.DateTime, default=utcnow)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)

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
    task_id = db.Column(db.String, nullable=False, unique=True)
    result_data = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=utcnow)

    job = db.relationship("Job", back_populates="results")

class ApplicationSettings(db.Model):
    __tablename__ = "application_settings"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String, unique=True, nullable=False)
    type = db.Column(db.String, nullable=False)
    value = db.Column(db.String)


# Automations: a named unit of functionality that runs on a schedule or in
# response to events. Two tiers (mirrors the theme registry): **system** ones
# ship with the app, are code-backed (a `handler` key into
# background_tasks.registry.HANDLERS) and route-locked against edit/delete;
# **custom** ones are AI-generated at runtime, carry their logic in `code`, and
# move through a generation status like themes/page_versions. A *run* of an
# automation reuses the Job table (jobs.automation_id), so the existing job UI /
# progress machinery is the run history.

# Generation status for custom automations (system rows are READY/ACCEPTED).
AUTOMATION_STATUS_IN_PROGRESS = "IN_PROGRESS"
AUTOMATION_STATUS_READY = "READY"
AUTOMATION_STATUS_FAILED = "FAILED"
AUTOMATION_STATUS_ACCEPTED = "ACCEPTED"

# How a trigger fires its automation.
TRIGGER_TYPE_SCHEDULE = "schedule"   # cron + next_run_at, driven by the dispatcher
TRIGGER_TYPE_EVENT = "event"         # an emitted domain event (see EVENTS)

# The fixed catalog of events an automation can subscribe to. Emission/dispatch
# for these is a later step; the constants pin the contract triggers reference.
EVENT_MEDIA_IMPORTED = "media_imported"
EVENT_MEDIA_INDEXED = "media_indexed"
EVENT_DUPLICATES_FOUND = "duplicates_found"
EVENT_MEDIA_MODIFIED = "media_modified"
EVENT_MEDIA_LABELED = "media_labeled"
EVENTS = {
    EVENT_MEDIA_IMPORTED: "Media imported",
    EVENT_MEDIA_INDEXED: "Media indexed",
    EVENT_DUPLICATES_FOUND: "Duplicates found",
    EVENT_MEDIA_MODIFIED: "Media modified",
    EVENT_MEDIA_LABELED: "Media labeled",
}

# Handler keys for system automations (registry in background_tasks.registry).
AUTOMATION_HANDLER_FILE_SYNC = "file_sync"
AUTOMATION_HANDLER_AUTO_ASSIGN_FACES = "auto_assign_faces"
AUTOMATION_HANDLER_DUPLICATE_SCAN = "duplicate_scan"
AUTOMATION_HANDLER_EXPORT_PHOTO_TAG = "export_photo_tag"
AUTOMATION_HANDLER_ASSIGN_LOCATION_NAME = "assign_location_name"
AUTOMATION_HANDLER_GEOTAG_FROM_NEIGHBORS = "geotag_from_neighbors"
AUTOMATION_HANDLER_CLASSIFY_LABELS = "classify_labels"

# Default match threshold for the auto-assign-faces automation, on the SAME 0-100
# UI similarity scale as the face screens (0 = least similar, 100 = most similar;
# overridable via Automation.config["threshold"]). The task scales it to a cosine
# cutoff against the live similarity band (compare_utils.ui_threshold_to_similarity).
# 50 is the neutral midpoint, matching the assignment screen's default.
AUTO_ASSIGN_FACES_DEFAULT_THRESHOLD = 80

# Default window (minutes) within which the geotag-from-neighbors automation borrows
# a GPS-tagged photo's coordinates (overridable via config["max_minutes"]).
GEOTAG_FROM_NEIGHBORS_DEFAULT_MINUTES = 30

# Defaults for the classify-labels automation. The threshold is a 0-100 strictness
# slider (image_classifier.get_clip_threshold maps it onto the practical ViT-B/32
# cosine band, with the neutral midpoint 50 ≈ 0.239 cosine — the measured sweet spot
# where most photos get their top label while weak matches are dropped). 50 is the
# default; raise it for precision, lower it for recall. At most
# CLASSIFY_LABELS_DEFAULT_MAX labels are kept per photo. Both via Automation.config.
CLASSIFY_LABELS_DEFAULT_THRESHOLD = 50
CLASSIFY_LABELS_DEFAULT_MAX = 4


# Localized display name + description for each built-in (system) automation, keyed by
# slug. The DB still stores the English name/description the migration seeded (the
# stable English source), but the UI shows these lazy_gettext strings so a system
# automation's name/description localizes per request. The source strings mirror the
# seeded English exactly, so the `en` locale renders identically. Custom (AI-authored)
# automations are not in here — their names are user data and are shown verbatim.
# Outside an app context a lazy string resolves to its English source.
SYSTEM_AUTOMATION_TEXT: dict[str, tuple] = {
    AUTOMATION_HANDLER_FILE_SYNC: (
        lazy_gettext("File sync"),
        lazy_gettext("Reconcile the photo index with the files on disk."),
    ),
    AUTOMATION_HANDLER_AUTO_ASSIGN_FACES: (
        lazy_gettext("Auto-assign faces"),
        lazy_gettext(
            "When a photo is indexed, assign each detected face to the one person it "
            "matches above the threshold — a face matching several people is left "
            "unassigned."
        ),
    ),
    AUTOMATION_HANDLER_EXPORT_PHOTO_TAG: (
        lazy_gettext("Export photo tag"),
        lazy_gettext(
            "When a photo is modified, write its people and/or location tags back into "
            "the photo file."
        ),
    ),
    AUTOMATION_HANDLER_ASSIGN_LOCATION_NAME: (
        lazy_gettext("Assign location name"),
        lazy_gettext(
            "When a photo is indexed, name its location from GPS — reuse a nearby named "
            "photo's name, then fall back to an OpenStreetMap lookup."
        ),
    ),
    AUTOMATION_HANDLER_GEOTAG_FROM_NEIGHBORS: (
        lazy_gettext("Geotag from neighbors"),
        lazy_gettext(
            "When a photo is indexed, give a GPS-less photo the coordinates of the "
            "closest-in-time photo that has GPS (e.g. a camera shot taken alongside "
            "phone shots)."
        ),
    ),
    AUTOMATION_HANDLER_CLASSIFY_LABELS: (
        lazy_gettext("Classify labels"),
        lazy_gettext(
            "When a photo is indexed, label it with the subjects, scenes, and "
            "activities it matches from your label vocabulary (offline CLIP image "
            "recognition)."
        ),
    ),
    AUTOMATION_HANDLER_DUPLICATE_SCAN: (
        lazy_gettext("Duplicate scan"),
        lazy_gettext(
            "Scan your indexed photos for duplicates on a schedule — results appear in "
            "the Remove Duplicates tool."
        ),
    ),
}


class Automation(db.Model):
    """A schedulable / event-driven unit of functionality (the definition).

    System automations (`is_system`) are code-backed via `handler` and locked in
    the UI; custom automations carry AI-generated Starlark. `published_code` is the
    live code the dispatchers run; `working_code` is the in-progress draft the
    builder edits before publishing (like a page's published vs working version).
    `enabled` is the master switch; per-trigger toggles live on the triggers. Runs
    are Job rows pointing back via jobs.automation_id; the build chat is the
    Conversation rows pointing back via conversations.automation_id."""

    __tablename__ = "automations"

    id = db.Column(db.Integer, primary_key=True)
    slug = db.Column(db.String, unique=True, nullable=False)
    name = db.Column(db.String, nullable=False)
    description = db.Column(db.String)
    is_system = db.Column(db.Boolean, nullable=False, default=False)
    enabled = db.Column(db.Boolean, nullable=False, default=False)
    handler = db.Column(db.String)          # system: key into HANDLERS; custom: NULL
    published_code = db.Column(db.Text)     # custom: the live Starlark; system: NULL
    working_code = db.Column(db.Text)       # custom: the in-progress draft; system: NULL
    config = db.Column(db.JSON)             # system: runtime-tunable settings (e.g. a threshold); custom: NULL
    status = db.Column(db.String, nullable=False, default=AUTOMATION_STATUS_READY)
    created_at = db.Column(db.DateTime, default=utcnow)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)

    triggers = db.relationship(
        "AutomationTrigger", back_populates="automation", cascade="all, delete-orphan"
    )
    jobs = db.relationship("Job", back_populates="automation")
    messages = db.relationship(
        "Conversation",
        back_populates="automation",
        cascade="all, delete-orphan",
        order_by="Conversation.id",
    )

    @property
    def display_name(self) -> str:
        """The name to show in the UI: the localized text for a built-in automation,
        else the stored (user/AI-authored) name verbatim."""
        text = SYSTEM_AUTOMATION_TEXT.get(self.slug) if self.is_system else None
        return str(text[0]) if text else self.name

    @property
    def display_description(self) -> str | None:
        """The description to show in the UI: the localized text for a built-in
        automation, else the stored description verbatim."""
        text = SYSTEM_AUTOMATION_TEXT.get(self.slug) if self.is_system else None
        return str(text[1]) if text else self.description

    def to_dict(self):
        return {
            'id': self.id,
            'slug': self.slug,
            'name': self.name,
            'description': self.description,
            'is_system': self.is_system,
            'enabled': self.enabled,
            'handler': self.handler,
            'config': self.config,
            'status': self.status,
            'triggers': [t.to_dict() for t in self.triggers],
        }


class AutomationTrigger(db.Model):
    """When an automation runs. A schedule trigger carries `cron` and the
    dispatcher bookkeeping (`next_run_at`/`last_run_at`); an event trigger carries
    `event_type`. `config` holds per-trigger JSON (event filters / handler args).
    Firing keys off `next_run_at <= now`, not exact cron-matching, so a due
    schedule still runs on the first dispatcher tick after its slot."""

    __tablename__ = "automation_triggers"

    id = db.Column(db.Integer, primary_key=True)
    automation_id = db.Column(
        db.Integer, db.ForeignKey("automations.id", ondelete="CASCADE"), nullable=False
    )
    trigger_type = db.Column(db.String, nullable=False)
    enabled = db.Column(db.Boolean, nullable=False, default=True)
    cron = db.Column(db.String)
    next_run_at = db.Column(db.DateTime)
    last_run_at = db.Column(db.DateTime)
    event_type = db.Column(db.String)
    config = db.Column(db.JSON)
    created_at = db.Column(db.DateTime, default=utcnow)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)

    automation = db.relationship("Automation", back_populates="triggers")

    def to_dict(self):
        return {
            'id': self.id,
            'automation_id': self.automation_id,
            'trigger_type': self.trigger_type,
            'enabled': self.enabled,
            'cron': self.cron,
            'event_type': self.event_type,
            'config': self.config,
            'next_run_at': self.next_run_at.isoformat() if self.next_run_at else None,
            'last_run_at': self.last_run_at.isoformat() if self.last_run_at else None,
        }


# PageVersion generation state machine (see docs/development/ai-page-builder.md).
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
    # Position in the nav's Pages strip; lower sorts first (see list_pages).
    tab_order = db.Column(db.Integer, nullable=False, default=0)
    # The live version shown in presentation, and the in-flight version a chat run
    # is generating into (or NULL — its presence is the UI-lock predicate). Plain
    # pointers into page_versions; the page <-> version relationship is circular, so
    # these use post_update to let the flush order them after the version exists.
    # use_alter marks this as a known cycle (page -> version -> page) so metadata DDL
    # can sort the DROP without warning; the real schema is init_db.py's raw SQL.
    published_version_id = db.Column(
        db.Integer, db.ForeignKey("page_versions.id", use_alter=True, name="fk_custom_pages_published_version"), nullable=True
    )
    working_version_id = db.Column(
        db.Integer, db.ForeignKey("page_versions.id", use_alter=True, name="fk_custom_pages_working_version"), nullable=True
    )
    created_at = db.Column(db.DateTime, default=utcnow)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)

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
    created_at = db.Column(db.DateTime, default=utcnow)
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
    # A conversation message belongs to exactly one owner: a page version (the page
    # builder) or an automation (the automation builder). Both FKs are nullable;
    # the owning feature sets one.
    version_id = db.Column(db.Integer, db.ForeignKey("page_versions.id", ondelete="CASCADE"), nullable=True)
    automation_id = db.Column(db.Integer, db.ForeignKey("automations.id", ondelete="CASCADE"), nullable=True)
    # CONVERSATION_TYPE_*: user | assistant | status | error. `status`/`error` are
    # display-only; only user/assistant are replayed to the model.
    type = db.Column(db.String, nullable=False)
    content = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow)

    version = db.relationship("PageVersion", back_populates="messages")
    automation = db.relationship("Automation", back_populates="messages")
