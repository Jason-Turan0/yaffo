"""The host API exposed to sandboxed automation Starlark.

Declared once in HOST_API and read in two places that must never diverge:
1. build_host_functions -- the live callables a script can invoke, bound to a
   session (the only way a sandboxed script reaches host state), and
2. render_host_api -- the agent-facing docs embedded in the automation system
   prompt, so the model writes against the real, current surface.

Add a capability = add one HostFunction entry; both the runtime and the docs pick
it up.
"""
import datetime
import inspect
from copy import deepcopy
from typing import get_type_hints, get_origin, get_args, Annotated
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Callable, Optional

from sqlalchemy.orm import Session

from yaffo.background_tasks.automation_sandbox import automation_actions as actions
from yaffo.background_tasks.automation_sandbox import automation_compare as compare
from yaffo.background_tasks.automation_sandbox import duplicates
from yaffo.background_tasks.automation_sandbox import location_suggestions
from yaffo.background_tasks.automation_sandbox import maintenance_actions as maintenance
from yaffo.background_tasks.automation_sandbox import preferences
from yaffo.background_tasks.automation_sandbox import undo
from yaffo.routes.filter_config import FILTERS
from yaffo.background_tasks.automation_sandbox.host_types import HostCall, reference, resolve_references


def _json_safe(value: Any) -> Any:
    """Coerce a host return value into types the starlark-pyo3 binding can marshal
    back into the sandbox (it JSON-encodes the value). DB rows carry types Starlark
    has no equivalent for -- dates (people.birthdate) and Decimals -- so map them to
    ISO strings / floats; dict/list are walked. Everything else passes through."""
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, datetime.date):  # date and datetime (datetime subclasses date)
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return value


def _signature_of(impl: Callable[..., Any]) -> str:
    """The script-facing signature, read from the impl: its name + parameters minus
    the leading `session` (which the bound callable hides)."""
    params = list(inspect.signature(impl).parameters.values())[1:]
    rendered = [
        param.name if param.default is inspect.Parameter.empty else f"{param.name}={param.default!r}"
        for param in params
    ]
    return f"{impl.__name__}({', '.join(rendered)})"


def _returns_of(impl: Callable[..., Any]) -> str:
    """A friendly description of the impl's return, read from its return annotation:
    `Annotated[T, "..."]` uses the prose metadata (the rich shape); `-> None` reads as
    "Nothing."; a bare `-> Any` varies; everything else shows its type (e.g. list[dict])."""
    annotation = inspect.signature(impl).return_annotation
    metadata = getattr(annotation, "__metadata__", None)
    if metadata:
        return str(metadata[0])
    if annotation is None or annotation is type(None):
        return "Nothing."
    if annotation is inspect.Signature.empty or annotation is Any:
        return "Varies — see the description."
    return str(annotation).replace("typing.", "")


@dataclass(frozen=True)
class HostFunction:
    """One callable exposed to sandboxed scripts. `impl` takes an injected run
    dependency as its first argument (`injects`: the run's "session" by default, or
    its "progress" reporter); the bound callable a script sees drops it. `name`,
    `signature`, and `returns` are introspected from `impl` (its name, its params
    minus the injected first one, and its return annotation), so the docs can't drift
    from the function. `description` and `example` are the prose docs. `summarize`
    turns a call's args into a friendly one-line action for the test/preview UI (it
    takes the session so it can resolve ids to file names / person names). `mutating`
    marks a capability that changes state -- recorded but NOT run in a test/preview."""
    impl: Callable[..., Any]
    description: str
    example: str
    summarize: Callable[[list[Any], Session], str] | None = None
    mutating: bool = False
    injects: str = "session"
    profiles: frozenset[str] = frozenset({"automation"})
    risk: str = "low"
    undo: Callable[[list[Any], Session], list[HostCall] | None] | None = None
    precondition: Callable[[list[Any], Session], str | None] | None = None
    setting_key: str | None = None
    # Returns a Job id: the work goes on in the background after the step "ran".
    # `job_page` is the endpoint where that job shows up, if any.
    starts_job: bool = False
    job_page: str | None = None

    def __post_init__(self) -> None:
        if not self.profiles or not self.profiles <= {"automation", "assistant"}:
            raise ValueError("Invalid host API profiles")
        if self.risk not in {"low", "medium", "high"}:
            raise ValueError("Invalid host function risk")
        if self.mutating and "assistant" in self.profiles and not self.setting_key:
            raise ValueError("Assistant mutations require a setting key")

    @property
    def returns_value(self) -> bool:
        annotation = get_type_hints(self.impl, include_extras=True).get("return", type(None))
        if get_origin(annotation) is Annotated:
            annotation = get_args(annotation)[0]
        return annotation not in (None, type(None))

    @property
    def name(self) -> str:
        return self.impl.__name__

    @property
    def signature(self) -> str:
        return _signature_of(self.impl)

    @property
    def returns(self) -> str:
        return _returns_of(self.impl)


HOST_API: tuple[HostFunction, ...] = (
    HostFunction(
        description=(
            "Read-only access to the app's data through the validated data_query "
            "contract. See the data_query tool for detailed schema. "
            "`query` is a dict naming a source, with optional per-column "
            'operator filters and a limit, e.g. {"source": "media_items", "year": '
            '{"eq": 2024}, "id": {"in": [1, 2, 3]}, "limit": 24}. Operators: eq, ne, '
            "lt, lte, gt, gte, contains, in. You never touch the database directly "
            "-- declare what you want and the server resolves it. Photo rows also "
            "carry `media_dir_id` and `relative_path` (the file's location, never an "
            "absolute path) -- pass media_dir_id to move_media_items."
        ),
        example='recent = data_query({"source": "media_items", "limit": 10})',
        impl=actions.data_query,
        profiles=frozenset({"automation", "assistant"}),
        summarize=actions.summarize_data_query,
        mutating=False,
    ),
    HostFunction(
        description=(
            "Report how far along the run is, so the run history shows a live "
            "percentage and an \"N of TOTAL processed\" line. Call it as you work "
            "through a set -- once per chunk or every few items -- passing how many "
            "are done so far and the total. Optional, but do it for any run that loops "
            "over many items."
        ),
        example='report_progress(done, len(ctx["media_item_ids"]))',
        impl=actions.report_progress,
        summarize=actions.summarize_report_progress,
        mutating=False,
        injects="progress",
    ),
    HostFunction(
        description=(
            "Add tags in one batched write. `tags` is a list of {media_item_id, name, "
            'value?} dicts: `name` is the tag (e.g. "beach"), `value` an optional value '
            "for name/value tags. Pass the whole set in one call (see <batching>)."
        ),
        example='tag_media_items([{"media_item_id": pid, "name": "beach"} for pid in ctx["media_item_ids"]])',
        impl=actions.tag_media_items,
        profiles=frozenset({"automation", "assistant"}),
        risk="low",
        setting_key="assistant_action_tag_media_items",
        undo=undo.tag_media_items,
        summarize=actions.summarize_tag_media_items,
        mutating=True,
    ),
    HostFunction(
        description=(
            "Rename files in one batched write. `renames` is a list of {media_item_id, "
            "new_name} dicts; each `new_name` is the new filename incl. extension, kept "
            "in the same folder. Pass the whole set in one call (see <batching>)."
        ),
        example='rename_files([{"media_item_id": pid, "new_name": "2024-06-01_beach.jpg"}])',
        impl=actions.rename_files,
        profiles=frozenset({"automation", "assistant"}),
        risk="high",
        setting_key="assistant_action_rename_files",
        summarize=actions.summarize_rename_files,
        mutating=True,
    ),
    HostFunction(
        description=(
            "Move photos in one batched write. `moves` is a list of {media_item_id, "
            "media_dir_id, target_path} dicts: each photo moves into `target_path` (a "
            "sub-folder of the media dir named by `media_dir_id`, created if needed), "
            "keeping its file name. Use a photo row's media_dir_id (from data_query) to "
            "move within its dir, or another media dir's id to move between dirs. A "
            "target outside the media dir, or an unknown media_dir_id, is skipped. Pass "
            "the whole set in one call (see <batching>)."
        ),
        example='move_media_items([{"media_item_id": r["id"], "media_dir_id": r["media_dir_id"], "target_path": "2024/06"} for r in rows])',
        impl=actions.move_media_items,
        profiles=frozenset({"automation", "assistant"}),
        risk="high",
        setting_key="assistant_action_move_media_items",
        summarize=actions.summarize_move_media_items,
        mutating=True,
    ),
    HostFunction(
        description=(
            "Assign faces to people in one batched write. `assignments` is a list of "
            "{face_id, person_id} dicts (use the face_id + person_id from match_people). "
            "Faces already assigned, and unknown person_ids, are skipped. Assign per "
            "face: a photo can contain several different people. Pass the whole set in "
            "one call (see <batching>)."
        ),
        example='assign_faces([{"face_id": fid, "person_id": pid}])',
        impl=actions.assign_faces,
        profiles=frozenset({"automation", "assistant"}),
        risk="low",
        setting_key="assistant_action_assign_faces",
        undo=undo.assign_faces,
        summarize=actions.summarize_assign_faces,
        mutating=True,
    ),
    HostFunction(
        description=(
            "Delete photos in one batched write. `media_item_ids` is a list of ids; each "
            "photo's file is sent to the OS trash (recoverable) and the photo with its "
            "faces/tags/labels is removed from the index. Destructive -- only delete "
            "what the request clearly asks to remove."
        ),
        example='delete_media_items([r["id"] for r in junk])',
        impl=actions.delete_media_items,
        profiles=frozenset({"automation", "assistant"}),
        risk="high",
        setting_key="assistant_action_delete_media_items",
        summarize=actions.summarize_delete_media_items,
        mutating=True,
    ),
    HostFunction(
        description=(
            "Create an album, or return the id of the existing album with that name. "
            "IDEMPOTENT on the name, so a repeating automation can call it on every run "
            "without failing or making duplicates. Read albums back with data_query "
            '(sources "albums" and "album_items"), never with a host call.'
        ),
        example='album_id = create_album("Beach 2024", "Everything from the coast")',
        impl=actions.create_album,
        profiles=frozenset({"automation", "assistant"}),
        risk="low",
        setting_key="assistant_action_create_album",
        undo=undo.create_album,
        summarize=actions.summarize_create_album,
        mutating=True,
    ),
    HostFunction(
        description=(
            "Rename an album / change its description. Membership is untouched. The "
            "name must stay unique."
        ),
        example='update_album(album_id, "Beach 2024", "Coast trip")',
        impl=actions.update_album,
        profiles=frozenset({"automation", "assistant"}),
        risk="low",
        setting_key="assistant_action_update_album",
        undo=undo.update_album,
        precondition=undo.album_exists,
        summarize=actions.summarize_update_album,
        mutating=True,
    ),
    HostFunction(
        description=(
            "Add photos to an album in one batched write. `media_item_ids` is a list of "
            "ids; photos already in the album are skipped, so re-running is safe. Pass "
            "the whole set in one call (see <batching>)."
        ),
        example='add_to_album(album_id, ctx["media_item_ids"])',
        impl=actions.add_to_album,
        profiles=frozenset({"automation", "assistant"}),
        risk="low",
        setting_key="assistant_action_add_to_album",
        undo=undo.add_to_album,
        precondition=undo.album_exists,
        summarize=actions.summarize_add_to_album,
        mutating=True,
    ),
    HostFunction(
        description=(
            "Remove photos from an album in one batched write. Only the membership goes "
            "-- the photos and their files are NOT deleted. Pass the whole set in one "
            "call (see <batching>)."
        ),
        example='remove_from_album(album_id, [r["id"] for r in stale])',
        impl=actions.remove_from_album,
        profiles=frozenset({"automation", "assistant"}),
        risk="low",
        setting_key="assistant_action_remove_from_album",
        undo=undo.remove_from_album,
        precondition=undo.album_exists,
        summarize=actions.summarize_remove_from_album,
        mutating=True,
    ),
    HostFunction(
        description=(
            "Delete an album and its contents list. The photos themselves are NOT "
            "deleted -- use delete_media_items for that."
        ),
        example="delete_album(album_id)",
        impl=actions.delete_album,
        profiles=frozenset({"automation", "assistant"}),
        risk="medium",
        setting_key="assistant_action_delete_album",
        precondition=undo.album_exists,
        summarize=actions.summarize_delete_album,
        mutating=True,
    ),
    HostFunction(
        description=(
            "Pin a photo that is in the album as its cover, or pass None to unpin it "
            "(the cover falls back to the album's first photo)."
        ),
        example="set_album_cover(album_id, media_item_id)",
        impl=actions.set_album_cover,
        profiles=frozenset({"automation", "assistant"}),
        risk="low",
        setting_key="assistant_action_set_album_cover",
        undo=undo.set_album_cover,
        precondition=undo.album_exists,
        summarize=actions.summarize_set_album_cover,
        mutating=True,
    ),
    HostFunction(
        description=(
            "Put an album's photos in this order. The listed photos come first, in the "
            "given order; photos not listed follow in their current order. Read the "
            "current order from album_items (position)."
        ),
        example='reorder_album(album_id, [r["id"] for r in sorted(rows, key=lambda r: r["date_taken"] or "")])',
        impl=actions.reorder_album,
        profiles=frozenset({"automation", "assistant"}),
        risk="low",
        setting_key="assistant_action_reorder_album",
        undo=undo.reorder_album,
        precondition=undo.album_exists,
        summarize=actions.summarize_reorder_album,
        mutating=True,
    ),
    HostFunction(
        description=(
            "How similar each face in the photo is to a known person, by face "
            "embeddings -- use it to decide whether to assign_faces."
        ),
        example="scores = face_similarity(media_item_id, person_id)",
        impl=compare.face_similarity,
        profiles=frozenset({"automation", "assistant"}),
        summarize=compare.summarize_face_similarity,
        mutating=False,
    ),
    HostFunction(
        description=(
            "Score every face in the photo against all known people -- the inverse "
            "of face_similarity, for identifying who is in a photo."
        ),
        example="matches = match_people(media_item_id)",
        impl=compare.match_people,
        profiles=frozenset({"automation", "assistant"}),
        summarize=compare.summarize_match_people,
        mutating=False
    ),
    HostFunction(
        description=(
            "The groups a finished duplicate scan found, by its job id (automation_runs or "
            "recent_jobs finds it; the duplicate_scan automation runs a scan). Each copy comes "
            "with its size, date, faces, tags and albums, to choose which one to keep."
        ),
        example='found = duplicate_groups(job_id, 50)',
        impl=duplicates.duplicate_groups,
        profiles=frozenset({"automation", "assistant"}),
        summarize=duplicates.summarize_duplicate_groups,
        mutating=False,
    ),
    HostFunction(
        description=(
            "Group unassigned faces that look alike, as the Faces page's Similarity grouping "
            "does (the oldest 2,000 unassigned faces). threshold is the page's 0-100 scale; "
            "higher means tighter clusters. Use it to find someone who appears often but isn't "
            "a person yet."
        ),
        example="clusters = suggest_face_clusters(60, 10)",
        impl=compare.suggest_face_clusters,
        profiles=frozenset({"automation", "assistant"}),
        summarize=compare.summarize_suggest_face_clusters,
        mutating=False,
    ),
    HostFunction(
        description=(
            "Unassigned faces that look like a person (the oldest 2,000 unassigned faces), "
            "most similar first, scored against the person's faces from the same stage of "
            "life. threshold is the Faces page's 0-100 scale. Review the scores before "
            "assigning."
        ),
        example="faces = find_similar_faces(person_id, 70, 100)",
        impl=compare.find_similar_faces,
        profiles=frozenset({"automation", "assistant"}),
        summarize=compare.summarize_find_similar_faces,
        mutating=False,
    ),
    HostFunction(
        impl=actions.untag_media_items,
        description='Remove exact name/value tags in a batch.',
        example='untag_media_items([{"media_item_id": 1, "name": "beach"}])',
        summarize=actions.summarize_untag_media_items,
        mutating=True,
        profiles=frozenset({"automation", "assistant"}),
        setting_key="assistant_action_untag_media_items",
        undo=undo.untag_media_items,
    ),
    HostFunction(
        impl=actions.unassign_faces,
        description='Remove face assignments; person_id optionally requires the current owner to match.',
        example='unassign_faces([{"face_id": 1, "person_id": 2}])',
        summarize=actions.summarize_unassign_faces,
        mutating=True,
        profiles=frozenset({"automation", "assistant"}),
        setting_key="assistant_action_unassign_faces",
        undo=undo.unassign_faces,
    ),
    HostFunction(
        impl=actions.ignore_faces,
        description=(
            "Ignore unassigned faces (e.g. strangers in the background), so they leave "
            "Unassigned Faces. Faces assigned to a person are left alone."
        ),
        example='ignore_faces([f["id"] for f in faces if f["status"] == "UNASSIGNED"])',
        summarize=actions.summarize_ignore_faces,
        mutating=True,
        profiles=frozenset({"automation", "assistant"}),
        risk="low",
        setting_key="assistant_action_ignore_faces",
        undo=undo.ignore_faces,
    ),
    HostFunction(
        impl=actions.unignore_faces,
        description="Return ignored faces to Unassigned Faces. Other faces are left alone.",
        example="unignore_faces([12, 13])",
        summarize=actions.summarize_unignore_faces,
        mutating=True,
        profiles=frozenset({"automation", "assistant"}),
        risk="low",
        setting_key="assistant_action_unignore_faces",
        undo=undo.unignore_faces,
    ),
    HostFunction(
        impl=actions.set_favorites,
        description='Set per-item favorite values (true, false, or null). Optional expected skips later edits.',
        example='set_favorites([{"id": 1, "favorite": True}])',
        summarize=actions.summarize_set_favorites,
        mutating=True,
        profiles=frozenset({"automation", "assistant"}),
        setting_key="assistant_action_set_favorites",
        undo=undo.set_favorites,
    ),
    HostFunction(
        impl=actions.set_media_dates,
        description='Set per-item camera-local ISO dates (or null), updating year/month. Optional expected skips later edits.',
        example='set_media_dates([{"id": 1, "date": "2024-06-01T12:00:00"}])',
        summarize=actions.summarize_set_media_dates,
        mutating=True,
        profiles=frozenset({"automation", "assistant"}),
        setting_key="assistant_action_set_media_dates",
        undo=undo.set_media_dates,
    ),
    HostFunction(
        description=(
            "Create a person (with no faces yet), or return the id of the existing person with "
            "that name. IDEMPOTENT on the name. Assign faces to the id with assign_faces."
        ),
        example='person_id = create_person("Billy")',
        impl=actions.create_person,
        profiles=frozenset({"automation", "assistant"}),
        risk="low",
        setting_key="assistant_action_create_person",
        undo=undo.create_person,
        summarize=actions.summarize_create_person,
        mutating=True,
    ),
    HostFunction(
        description=(
            "Rename a person. The name must not belong to another person (merge_people them "
            "instead). The new name is written into their photos' files by export automations."
        ),
        example='rename_person(person_id, "Billy Smith")',
        impl=actions.rename_person,
        profiles=frozenset({"automation", "assistant"}),
        risk="low",
        setting_key="assistant_action_rename_person",
        undo=undo.rename_person,
        precondition=undo.person_exists,
        summarize=actions.summarize_rename_person,
        mutating=True,
    ),
    HostFunction(
        description=(
            "Merge two people who are the same person: every face of the source moves to the "
            "target, and the source is deleted. Can't be undone."
        ),
        example="merge_people(duplicate_id, person_id)",
        impl=actions.merge_people,
        profiles=frozenset({"automation", "assistant"}),
        risk="high",
        setting_key="assistant_action_merge_people",
        precondition=undo.people_exist,
        summarize=actions.summarize_merge_people,
        mutating=True,
    ),
    HostFunction(
        description=(
            "Delete a person. Their faces become unassigned (the photos stay). Can't be undone."
        ),
        example="delete_person(person_id)",
        impl=actions.delete_person,
        profiles=frozenset({"automation", "assistant"}),
        risk="high",
        setting_key="assistant_action_delete_person",
        precondition=undo.person_exists,
        summarize=actions.summarize_delete_person,
        mutating=True,
    ),
    HostFunction(
        description=(
            "Set a person's birthdate (YYYY-MM-DD), or None to clear it. It decides which "
            "stage of life each of their faces is compared in, so their matching is rebuilt."
        ),
        example='set_person_birthdate(person_id, "2015-06-01")',
        impl=actions.set_person_birthdate,
        profiles=frozenset({"automation", "assistant"}),
        risk="low",
        setting_key="assistant_action_set_person_birthdate",
        undo=undo.set_person_birthdate,
        precondition=undo.person_exists,
        summarize=actions.summarize_set_person_birthdate,
        mutating=True,
    ),
    HostFunction(
        description=(
            "Add a label to the auto-classifier's vocabulary (Settings → Labels), or return "
            "the existing label with that name. prompt is the text photos are matched by "
            "(default 'a photo of <name>'). Photos only get it when they're classified again "
            "(the classify_labels automation)."
        ),
        example='label = add_label_to_vocabulary("sailboat", "a photo of a sailboat on the water")',
        impl=actions.add_label_to_vocabulary,
        profiles=frozenset({"assistant"}),
        risk="low",
        setting_key="assistant_action_add_label_to_vocabulary",
        undo=undo.add_label_to_vocabulary,
        summarize=actions.summarize_add_label_to_vocabulary,
        mutating=True,
    ),
    HostFunction(
        description=(
            "Remove a label from the classifier's vocabulary by its id (classification_labels). "
            "Every photo loses that label; re-adding it needs a new classification run."
        ),
        example="delete_label(label_id)",
        impl=actions.delete_label,
        profiles=frozenset({"assistant"}),
        risk="medium",
        setting_key="assistant_action_delete_label",
        precondition=undo.label_exists,
        summarize=actions.summarize_delete_label,
        mutating=True,
    ),
    # ---- preferences (assistant only) ----
    HostFunction(
        description="Make an existing theme the app's theme, by slug (built-in or a published custom theme).",
        example='set_default_theme("darkroom")',
        impl=preferences.set_default_theme,
        profiles=frozenset({"assistant"}),
        risk="low",
        setting_key="assistant_action_set_default_theme",
        undo=preferences.undo_set_default_theme,
        precondition=preferences.theme_exists,
        summarize=preferences.summarize_set_default_theme,
        mutating=True,
    ),
    HostFunction(
        description=(
            "Set the app's language by code (en, de, es, fr, zh, hi, ar), or None to follow "
            "the browser's language."
        ),
        example='set_locale("de")',
        impl=preferences.set_locale,
        profiles=frozenset({"assistant"}),
        risk="low",
        setting_key="assistant_action_set_locale",
        undo=preferences.undo_set_locale,
        summarize=preferences.summarize_set_locale,
        mutating=True,
    ),
    HostFunction(
        description='Show distances in miles ("mi") or kilometers ("km").',
        example='set_distance_unit("km")',
        impl=preferences.set_distance_unit,
        profiles=frozenset({"assistant"}),
        risk="low",
        setting_key="assistant_action_set_distance_unit",
        undo=preferences.undo_set_distance_unit,
        summarize=preferences.summarize_set_distance_unit,
        mutating=True,
    ),
    HostFunction(
        description=(
            "Choose which filters the gallery sidebar shows and in what order: [{key, visible}] "
            "in display order (Settings → Filters). Filters left out follow the listed ones, shown. "
            "Keys: " + ", ".join(f.key for f in FILTERS) + "."
        ),
        example='set_filter_layout([{"key": "people", "visible": True}, {"key": "device", "visible": False}])',
        impl=preferences.set_filter_layout,
        profiles=frozenset({"assistant"}),
        risk="low",
        setting_key="assistant_action_set_filter_layout",
        undo=preferences.undo_set_filter_layout,
        summarize=preferences.summarize_set_filter_layout,
        mutating=True,
    ),
    # ---- maintenance (assistant only) ----
    HostFunction(
        description=(
            "Cancel a pending or running background job by its id (recent_jobs lists them), "
            "as its Cancel button does. Work already done stays done; it can't be resumed, "
            "only started again."
        ),
        example='cancel_job("3f2c...")',
        impl=maintenance.cancel_job,
        profiles=frozenset({"assistant"}),
        risk="medium",
        setting_key="assistant_action_cancel_job",
        precondition=maintenance.job_active,
        summarize=maintenance.summarize_cancel_job,
        mutating=True,
    ),
    HostFunction(
        description=(
            "Turn an automation on or off by its slug (settings_summary lists every automation). "
            "Only the switch changes, never its code or settings."
        ),
        example='set_automation_enabled("file-sync", False)',
        impl=maintenance.set_automation_enabled,
        profiles=frozenset({"assistant"}),
        risk="low",
        setting_key="assistant_action_set_automation_enabled",
        undo=maintenance.undo_set_automation_enabled,
        precondition=maintenance.automation_exists,
        summarize=maintenance.summarize_set_automation_enabled,
        mutating=True,
    ),
    HostFunction(
        description=(
            "Run any existing automation now, even if it is disabled or has no triggers. "
            "The optional scope is {type: 'everything'} (default), {type: 'media_dirs', "
            "media_dir_ids: [...]}, {type: 'files', media_item_ids: [...]}, or {type: 'folders', "
            "folder_paths: [...]}. File IDs come from data_query; folders must be inside configured "
            "media directories. File sync accepts directories and folders, not individual files. "
            "Review the automation's Run history for progress and outcome."
        ),
        example='run_automation("export_photo_tag", {"type": "files", "media_item_ids": [1, 2]})',
        impl=maintenance.run_automation,
        profiles=frozenset({"assistant"}),
        risk="high",
        setting_key="assistant_action_run_automation",
        precondition=maintenance.automation_runnable,
        summarize=maintenance.summarize_run_automation,
        mutating=True,
    ),
    HostFunction(
        description=(
            "Index items again from their files (e.g. after a failed or wrong index). Their faces "
            "are detected again, so person assignments on them are removed. Starts a background job."
        ),
        example="reindex_media([r[\"id\"] for r in rows])",
        impl=maintenance.reindex_media,
        profiles=frozenset({"assistant"}),
        risk="medium",
        setting_key="assistant_action_reindex_media",
        precondition=maintenance.thumbnails_configured,
        summarize=maintenance.summarize_reindex_media,
        mutating=True,
        starts_job=True,
        job_page="utilities_index_photos",
    ),
    HostFunction(
        description=(
            "Write missing face crops and video posters again from the photos and videos (the "
            "files the index points at in the thumbnail folder, shown as broken images). Faces keep "
            "their people and ignored status; nothing is re-detected. Starts a background job."
        ),
        example="regenerate_thumbnails()",
        impl=maintenance.regenerate_thumbnails,
        profiles=frozenset({"assistant"}),
        risk="low",
        setting_key="assistant_action_regenerate_thumbnails",
        precondition=maintenance.thumbnails_missing,
        summarize=maintenance.summarize_regenerate_thumbnails,
        mutating=True,
        starts_job=True,
        job_page="utilities_index_photos",
    ),
    HostFunction(
        description=(
            "Fix faces in inconsistent states (see face_consistency): linked to a person but not "
            "assigned, stuck processing, or ignored but still linked (the link is removed)."
        ),
        example="repair_face_statuses()",
        impl=maintenance.repair_face_statuses,
        profiles=frozenset({"assistant"}),
        risk="medium",
        setting_key="assistant_action_repair_face_statuses",
        precondition=maintenance.faces_need_repair,
        summarize=maintenance.summarize_repair_face_statuses,
        mutating=True,
    ),
    HostFunction(
        impl=actions.set_coordinates,
        description=(
            "Set per-item GPS coordinates, or null for both to clear them. To copy a place from "
            "another photo, read its latitude and longitude with data_query and pass them. "
            "Optional expected ([latitude, longitude]) skips later edits."
        ),
        example='set_coordinates([{"id": i, "latitude": 48.8584, "longitude": 2.2945} for i in ids])',
        summarize=actions.summarize_set_coordinates,
        mutating=True,
        profiles=frozenset({"automation", "assistant"}),
        risk="low",
        setting_key="assistant_action_set_coordinates",
        undo=undo.set_coordinates,
    ),
    HostFunction(
        description=(
            "For each photo with GPS coordinates (up to 500), the location name of the closest "
            "already-named photo within radius_km (default: the Locations page's nearby radius). "
            "Offline: it never looks names up online."
        ),
        example="suggestions = suggest_location_names(ids)",
        impl=location_suggestions.suggest_location_names,
        profiles=frozenset({"automation", "assistant"}),
        summarize=location_suggestions.summarize_suggest_location_names,
        mutating=False,
    ),
    HostFunction(
        impl=actions.set_location_names,
        description='Set per-item location names (or null). Optional expected skips later edits.',
        example='set_location_names([{"id": 1, "location_name": "Yellowstone"}])',
        summarize=actions.summarize_set_location_names,
        mutating=True,
        profiles=frozenset({"automation", "assistant"}),
        setting_key="assistant_action_set_location_names",
        undo=undo.set_location_names,
    ),
)


def _bind(impl: Callable[..., Any], dependency: Any) -> Callable[..., Any]:
    def call(*args: Any) -> Any:
        # Coerce the return to sandbox-marshalable types (e.g. DB dates -> ISO strings).
        return _json_safe(impl(dependency, *args))
    return call


def _dependency(fn: HostFunction, session: Session, progress: Any) -> Any:
    """The run dependency injected as `fn.impl`'s first arg: the run's progress
    reporter for fn.injects == 'progress', else the session."""
    return progress if fn.injects == "progress" else session


def build_host_functions(
    session: Session, progress: Any = None, *, profile: str = "automation", include_mutating: bool = True,
) -> dict[str, Callable[..., Any]]:
    """The curated host callables for a run, derived from HOST_API and bound to their
    run dependency -- the `session` (so each reads within the caller's transaction),
    or the `progress` reporter for report_progress. Pass as `functions` to run_starlark.
    `include_mutating=False` binds only the read-only functions."""
    return {
        fn.name: _bind(fn.impl, _dependency(fn, session, progress))
        for fn in host_api(profile) if include_mutating or not fn.mutating
    }


def host_api(profile: str = "automation") -> tuple[HostFunction, ...]:
    if profile not in {"automation", "assistant"}:
        raise ValueError(f"Unknown host API profile: {profile}")
    return tuple(fn for fn in HOST_API if profile in fn.profiles)


_HOST_BY_NAME = {fn.name: fn for fn in HOST_API}


def host_function(name: str, profile: str = "automation") -> HostFunction:
    """The HostFunction `name` in `profile`; unknown names and functions outside the
    profile raise, so a stored call can't reach anything the profile doesn't bind."""
    fn = _HOST_BY_NAME.get(name)
    if fn is None or profile not in fn.profiles:
        raise ValueError(f"Unknown host function: {name}")
    return fn


def summarize_call(call: HostCall, session: Session, mutations: list[HostCall] | None = None) -> str:
    """A friendly one-line description of a recorded call for the test UI (e.g.
    "Looking up photos"), resolving ids against `session`; falls back to the call's
    signature/name."""
    if mutations is not None and call.name in {"add_to_album", "remove_from_album", "update_album", "delete_album"}:
        if call.args and isinstance(call.args[0], str) and call.args[0].startswith("$ref:"):
            targets = {i: c for i, c in enumerate(mutations) if c.name == "create_album"}
            target = resolve_references(call.args[0], targets)
            title = str(target.args[0])
            if call.name in {"add_to_album", "remove_from_album"}:
                verb, prep = ("Add", "to") if call.name == "add_to_album" else ("Remove", "from")
                return f"{verb} {len(call.args[1])} photo(s) {prep} the new album '{title}'"
    fn = _HOST_BY_NAME.get(call.name)
    if fn is not None and fn.summarize is not None:
        try:
            return fn.summarize(call.args, session)
        except Exception:
            pass
    return fn.signature if fn is not None else call.name


def build_recording_host_functions(
    session: Session, progress: Any = None, *, profile: str = "automation",
) -> tuple[dict[str, Callable[..., Any]], list[HostCall]]:
    """Like build_host_functions, but every invocation is appended to the returned
    `calls` list before the real impl runs. The read-only surface still executes so
    the script gets live data; mutating actions are recorded but not performed (a
    test/preview changes nothing). `progress` is None in a preview, so report_progress
    no-ops."""
    calls: list[HostCall] = []
    mutation_count = 0
    references: dict[int, Any] = {}

    def record(fn: HostFunction) -> Callable[..., Any]:
        bound = _bind(fn.impl, _dependency(fn, session, progress))

        def call(*args: Any) -> Any:
            nonlocal mutation_count
            # Validate arity even though preview skips the mutating implementation.
            inspect.signature(fn.impl).bind(_dependency(fn, session, progress), *args)
            frozen = deepcopy(list(args))
            resolve_references(frozen, references)
            if not fn.mutating:
                # A read cannot observe an album that has not been created yet.
                if resolve_references(frozen, {i: None for i in references}) != frozen:
                    raise ValueError("Preview references can only be passed to mutating calls")
                result = bound(*args)
            else:
                result = reference(mutation_count) if fn.returns_value else None
                if fn.returns_value:
                    references[mutation_count] = result
                mutation_count += 1
            calls.append(HostCall(name=fn.name, args=frozen))
            return result
        return call

    return {fn.name: record(fn) for fn in host_api(profile)}, calls


def render_host_api(
    profile: str = "automation", *, include_mutating: bool = True, mutations: Optional[frozenset[str]] = None,
) -> str:
    """The host API as agent-facing docs for the automation system prompt -- one
    block per callable. Single source with build_host_functions, so the advertised
    API can't drift from what the sandbox actually provides. `include_mutating=False`
    documents only the read-only functions (for a caller that binds only those);
    `mutations` narrows the mutating ones to those names (the assistant binds only
    the changes switched on in Settings)."""
    include_mutating = include_mutating and mutations != frozenset()
    blocks: list[str] = []
    if include_mutating:
        blocks.append(
            "Preview: mutations are recorded, never executed. A mutation returning a value "
            "returns an opaque $ref:N token (zero-based mutation index). Only pass this token "
            "unchanged to later mutating calls; do not compute with it or use it in reads."
        )
    blocks.append("data_query returns at most 5,000 rows per call. Runs have time, call and output limits.")
    for fn in host_api(profile):
        if fn.mutating and (not include_mutating or (mutations is not None and fn.name not in mutations)):
            continue
        blocks.append(
            f"{fn.signature}\n"
            f"  {fn.description}\n"
            f"  Returns: {fn.returns}\n"
            f"  Example: {fn.example}\n"
            f"  Mutating: {fn.mutating or False}"
        )
    return "\n\n".join(blocks)
