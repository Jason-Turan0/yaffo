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
from typing import Any, Callable

from sqlalchemy.orm import Session

from yaffo.background_tasks.automation_sandbox import automation_actions as actions
from yaffo.background_tasks.automation_sandbox import automation_compare as compare
from yaffo.background_tasks.automation_sandbox import undo
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
    uses_network: bool = False
    setting_key: str | None = None

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


def build_host_functions(session: Session, progress: Any = None, *, profile: str = "automation") -> dict[str, Callable[..., Any]]:
    """The curated host callables for a run, derived from HOST_API and bound to their
    run dependency -- the `session` (so each reads within the caller's transaction),
    or the `progress` reporter for report_progress. Pass as `functions` to run_starlark."""
    return {fn.name: _bind(fn.impl, _dependency(fn, session, progress)) for fn in host_api(profile)}


def host_api(profile: str = "automation") -> tuple[HostFunction, ...]:
    if profile not in {"automation", "assistant"}:
        raise ValueError(f"Unknown host API profile: {profile}")
    return tuple(fn for fn in HOST_API if profile in fn.profiles)


_HOST_BY_NAME = {fn.name: fn for fn in HOST_API}


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


def render_host_api(profile: str = "automation") -> str:
    """The host API as agent-facing docs for the automation system prompt -- one
    block per callable. Single source with build_host_functions, so the advertised
    API can't drift from what the sandbox actually provides."""
    blocks: list[str] = [
        "Preview: mutations are recorded, never executed. A mutation returning a value "
        "returns an opaque $ref:N token (zero-based mutation index). Only pass this token "
        "unchanged to later mutating calls; do not compute with it or use it in reads. "
        "data_query returns at most 5,000 rows per call. Runs have time, call and output limits."
    ]
    for fn in host_api(profile):
        blocks.append(
            f"{fn.signature}\n"
            f"  {fn.description}\n"
            f"  Returns: {fn.returns}\n"
            f"  Example: {fn.example}"
            f"  Mutating: {fn.mutating or False}"
        )
    return "\n\n".join(blocks)
