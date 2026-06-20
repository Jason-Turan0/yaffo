"""The host API exposed to sandboxed automation Starlark.

Declared once in HOST_API and read in two places that must never diverge:
1. build_host_functions -- the live callables a script can invoke, bound to a
   session (the only way a sandboxed script reaches host state), and
2. render_host_api -- the agent-facing docs embedded in the automation system
   prompt, so the model writes against the real, current surface.

Add a capability = add one HostFunction entry; both the runtime and the docs pick
it up.
"""
import inspect
from dataclasses import dataclass
from typing import Any, Callable

from sqlalchemy.orm import Session

from yaffo.background_tasks.automation_sandbox import automation_actions as actions
from yaffo.background_tasks.automation_sandbox import automation_compare as compare


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
    """One callable exposed to sandboxed scripts. `impl` takes the session as its
    first argument; the bound callable a script sees drops it. `name`, `signature`,
    and `returns` are introspected from `impl` (its name, its params minus session,
    and its return annotation), so the docs can't drift from the function.
    `description` and `example` are the prose docs. `summarize` turns a call's args
    into a friendly one-line action for the test/preview UI (it takes the session so
    it can resolve ids to file names / person names). `mutating` marks a capability
    that changes state -- recorded but NOT run in a test/preview."""
    impl: Callable[..., Any]
    description: str
    example: str
    summarize: Callable[[list[Any], Session], str] | None = None
    mutating: bool = False

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
            'operator filters and a limit, e.g. {"source": "photos", "year": '
            '{"eq": 2024}, "id": {"in": [1, 2, 3]}, "limit": 24}. Operators: eq, ne, '
            "lt, lte, gt, gte, contains, in. You never touch the database directly "
            "-- declare what you want and the server resolves it. Photo rows also "
            "carry `media_dir_id` and `relative_path` (the file's location, never an "
            "absolute path) -- pass media_dir_id to move_photos."
        ),
        example='recent = data_query({"source": "photos", "limit": 10})',
        impl=actions.data_query,
        summarize=actions.summarize_data_query,
        mutating=False,
    ),
    HostFunction(
        description=(
            "Add tags in one batched write. `tags` is a list of {photo_id, name, "
            'value?} dicts: `name` is the tag (e.g. "beach"), `value` an optional value '
            "for name/value tags. Pass the whole set in one call (see <batching>)."
        ),
        example='tag_photos([{"photo_id": pid, "name": "beach"} for pid in ctx["photo_ids"]])',
        impl=actions.tag_photos,
        summarize=actions.summarize_tag_photos,
        mutating=True,
    ),
    HostFunction(
        description=(
            "Rename files in one batched write. `renames` is a list of {photo_id, "
            "new_name} dicts; each `new_name` is the new filename incl. extension, kept "
            "in the same folder. Pass the whole set in one call (see <batching>)."
        ),
        example='rename_files([{"photo_id": pid, "new_name": "2024-06-01_beach.jpg"}])',
        impl=actions.rename_files,
        summarize=actions.summarize_rename_files,
        mutating=True,
    ),
    HostFunction(
        description=(
            "Move photos in one batched write. `moves` is a list of {photo_id, "
            "media_dir_id, target_path} dicts: each photo moves into `target_path` (a "
            "sub-folder of the media dir named by `media_dir_id`, created if needed), "
            "keeping its file name. Use a photo row's media_dir_id (from data_query) to "
            "move within its dir, or another media dir's id to move between dirs. A "
            "target outside the media dir, or an unknown media_dir_id, is skipped. Pass "
            "the whole set in one call (see <batching>)."
        ),
        example='move_photos([{"photo_id": r["id"], "media_dir_id": r["media_dir_id"], "target_path": "2024/06"} for r in rows])',
        impl=actions.move_photos,
        summarize=actions.summarize_move_photos,
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
        summarize=actions.summarize_assign_faces,
        mutating=True,
    ),
    HostFunction(
        description=(
            "How similar each face in the photo is to a known person, by face "
            "embeddings -- use it to decide whether to assign_faces."
        ),
        example="scores = face_similarity(photo_id, person_id)",
        impl=compare.face_similarity,
        summarize=compare.summarize_face_similarity,
        mutating=False,
    ),
    HostFunction(
        description=(
            "Score every face in the photo against all known people -- the inverse "
            "of face_similarity, for identifying who is in a photo."
        ),
        example="matches = match_people(photo_id)",
        impl=compare.match_people,
        summarize=compare.summarize_match_people,
        mutating=False
    ),
)


def _bind(impl: Callable[..., Any], session: Session) -> Callable[..., Any]:
    def call(*args: Any) -> Any:
        return impl(session, *args)
    return call


def build_host_functions(session: Session) -> dict[str, Callable[..., Any]]:
    """The curated host callables for a run, derived from HOST_API and bound to
    `session` so each reads within the caller's transaction. Pass as `functions`
    to run_starlark."""
    return {fn.name: _bind(fn.impl, session) for fn in HOST_API}


@dataclass(frozen=True)
class HostCall:
    """One host-API invocation a script made, captured by a recording run so a
    test/preview can show the actions performed. `name` is the host function,
    `args` the arguments the script passed (e.g. the data_query dict)."""
    name: str
    args: list[Any]


_HOST_BY_NAME = {fn.name: fn for fn in HOST_API}


def summarize_call(call: HostCall, session: Session) -> str:
    """A friendly one-line description of a recorded call for the test UI (e.g.
    "Looking up photos"), resolving ids against `session`; falls back to the call's
    signature/name."""
    fn = _HOST_BY_NAME.get(call.name)
    if fn is not None and fn.summarize is not None:
        try:
            return fn.summarize(call.args, session)
        except Exception:
            pass
    return fn.signature if fn is not None else call.name


def build_recording_host_functions(
    session: Session,
) -> tuple[dict[str, Callable[..., Any]], list[HostCall]]:
    """Like build_host_functions, but every invocation is appended to the returned
    `calls` list before the real impl runs. The read-only surface still executes so
    the script gets live data; the same hook is where a future mutating capability
    would be recorded-but-not-performed for a true dry run."""
    calls: list[HostCall] = []

    def record(fn: HostFunction) -> Callable[..., Any]:
        bound = _bind(fn.impl, session)

        def call(*args: Any) -> Any:
            calls.append(HostCall(name=fn.name, args=list(args)))
            # Reads still run so the script gets live data; mutating actions are
            # recorded but not performed -- a test/preview changes nothing.
            return None if fn.mutating else bound(*args)
        return call

    return {fn.name: record(fn) for fn in HOST_API}, calls


def render_host_api() -> str:
    """The host API as agent-facing docs for the automation system prompt -- one
    block per callable. Single source with build_host_functions, so the advertised
    API can't drift from what the sandbox actually provides."""
    blocks: list[str] = []
    for fn in HOST_API:
        blocks.append(
            f"{fn.signature}\n"
            f"  {fn.description}\n"
            f"  Returns: {fn.returns}\n"
            f"  Example: {fn.example}"
            f"  Mutating: {fn.mutating or False}"
        )
    return "\n\n".join(blocks)
