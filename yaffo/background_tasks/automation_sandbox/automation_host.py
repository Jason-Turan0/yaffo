"""The host API exposed to sandboxed automation Starlark.

Declared once in HOST_API and read in two places that must never diverge:
1. build_host_functions -- the live callables a script can invoke, bound to a
   session (the only way a sandboxed script reaches host state), and
2. render_host_api -- the agent-facing docs embedded in the automation system
   prompt, so the model writes against the real, current surface.

Add a capability = add one HostFunction entry; both the runtime and the docs pick
it up.
"""
from dataclasses import dataclass
from typing import Any, Callable

from sqlalchemy.orm import Session

from yaffo.background_tasks.automation_sandbox import automation_actions as actions
from yaffo.background_tasks.automation_sandbox import automation_compare as compare


@dataclass(frozen=True)
class HostFunction:
    """One callable exposed to sandboxed scripts. `impl` takes the session as its
    first argument; the bound callable a script sees drops it. `signature`,
    `description`, `returns`, `example` are the agent docs. `summarize` turns a
    call's args into a friendly one-line action for the test/preview UI (it takes the
    session so it can resolve ids to file names / person names). `mutating` marks a
    capability that changes state -- recorded but NOT run in a test/preview."""
    name: str
    signature: str
    description: str
    returns: str
    example: str
    impl: Callable[..., Any]
    summarize: Callable[[list[Any], Session], str] | None = None
    mutating: bool = False


HOST_API: tuple[HostFunction, ...] = (
    HostFunction(
        name="data_query",
        signature="data_query(query)",
        description=(
            "Read-only access to the app's data through the validated data_query "
            "contract. See the data_query tool for detailed schema. "
            "`query` is a dict naming a source, with optional per-column "
            'operator filters and a limit, e.g. {"source": "photos", "year": '
            '{"eq": 2024}, "id": {"in": [1, 2, 3]}, "limit": 24}. Operators: eq, ne, '
            "lt, lte, gt, gte, contains, in. You never touch the database directly "
            "-- declare what you want and the server resolves it. Photo rows also "
            "carry `media_dir_id` and `relative_path` (the file's location, never an "
            "absolute path) -- pass media_dir_id to move_photo."
        ),
        returns="A list of row dicts; or a single number/object for count/range queries.",
        example='recent = data_query({"source": "photos", "limit": 10})',
        impl=actions.data_query,
        summarize=actions.summarize_data_query,
        mutating=False,
    ),
    HostFunction(
        name="tag_photos",
        signature="tag_photos(tags)",
        description=(
            "Add many tags in one batched write (preferred — see <batching>). `tags` is "
            "a list of {photo_id, name, value?} dicts: `name` is the tag (e.g. "
            '"beach"), `value` an optional value for name/value tags.'
        ),
        returns="Nothing.",
        example='tag_photos([{"photo_id": pid, "name": "beach"} for pid in ctx["photo_ids"]])',
        impl=actions.tag_photos,
        summarize=actions.summarize_tag_photos,
        mutating=True,
    ),
    HostFunction(
        name="tag_photo",
        signature="tag_photo(photo_id, name, value=None)",
        description=(
            "Add a tag to a single photo. Prefer tag_photos for more than one (see "
            "<batching>). `name` is the tag (e.g. \"beach\"); `value` is an optional "
            "value for name/value tags (e.g. name=\"rating\", value=5)."
        ),
        returns="Nothing.",
        example='tag_photo(photo_id, "beach")',
        impl=actions.tag_photo,
        summarize=actions.summarize_tag_photo,
        mutating=True,
    ),
    HostFunction(
        name="rename_files",
        signature="rename_files(renames)",
        description=(
            "Rename many files in one batched write (preferred — see <batching>). "
            "`renames` is a list of {photo_id, new_name} dicts; each `new_name` is the "
            "new filename incl. extension, kept in the same folder."
        ),
        returns="Nothing.",
        example='rename_files([{"photo_id": pid, "new_name": "2024-06-01_beach.jpg"}])',
        impl=actions.rename_files,
        summarize=actions.summarize_rename_files,
        mutating=True,
    ),
    HostFunction(
        name="rename_file",
        signature="rename_file(photo_id, new_name)",
        description=(
            "Rename one photo's file on disk to `new_name` (kept in the same folder) "
            "and update its stored path. Prefer rename_files for more than one (see "
            "<batching>)."
        ),
        returns="Nothing.",
        example='rename_file(photo_id, "2024-06-01_beach.jpg")',
        impl=actions.rename_file,
        summarize=actions.summarize_rename_file,
        mutating=True,
    ),
    HostFunction(
        name="move_photos",
        signature="move_photos(moves)",
        description=(
            "Move many photos in one batched write (preferred — see <batching>). "
            "`moves` is a list of {photo_id, media_dir_id, target_path} dicts: each "
            "photo moves into `target_path` (a sub-folder of the media dir named by "
            "`media_dir_id`, created if needed), keeping its file name. Use a photo "
            "row's media_dir_id (from data_query) to move within its dir, or another "
            "media dir's id to move between dirs. A target outside the media dir, or an "
            "unknown media_dir_id, is skipped."
        ),
        returns="Nothing.",
        example='move_photos([{"photo_id": r["id"], "media_dir_id": r["media_dir_id"], "target_path": "2024/06"} for r in rows])',
        impl=actions.move_photos,
        summarize=actions.summarize_move_photos,
        mutating=True,
    ),
    HostFunction(
        name="move_photo",
        signature="move_photo(photo_id, media_dir_id, target_path)",
        description=(
            "Move one photo into `target_path` (a sub-folder of the media dir named "
            "by `media_dir_id`, created if needed), keeping its file name. Prefer "
            "move_photos for more than one (see <batching>). A target outside the "
            "media dir, or an unknown media_dir_id, is refused."
        ),
        returns="Nothing.",
        example='move_photo(photo_id, media_dir_id, "2024/06")',
        impl=actions.move_photo,
        summarize=actions.summarize_move_photo,
        mutating=True,
    ),
    HostFunction(
        name="assign_faces",
        signature="assign_faces(assignments)",
        description=(
            "Assign many faces to people in one batched write (preferred — see "
            "<batching>). `assignments` is a list of {face_id, person_id} dicts (use "
            "the face_id + person_id from match_people). Faces already assigned, and "
            "unknown person_ids, are skipped. (Assign per face: a photo can contain "
            "several different people.)"
        ),
        returns="Nothing.",
        example='assign_faces([{"face_id": fid, "person_id": pid}])',
        impl=actions.assign_faces,
        summarize=actions.summarize_assign_faces,
        mutating=True,
    ),
    HostFunction(
        name="assign_face",
        signature="assign_face(face_id, person_id)",
        description=(
            "Assign an existing person (by id) to one detected face -- use the "
            "face_id + person_id from match_people. Prefer assign_faces for more than "
            "one (see <batching>). A face already assigned to someone is left as-is; "
            "an unknown person_id is a no-op."
        ),
        returns="Nothing.",
        example="assign_face(face_id, person_id)",
        impl=actions.assign_face,
        summarize=actions.summarize_assign_face,
        mutating=True,
    ),
    HostFunction(
        name="face_similarity",
        signature="face_similarity(photo_id, person_id)",
        description=(
            "How similar each face in the photo is to a known person, by face "
            "embeddings -- use it to decide whether to assign_face. Empty if the "
            "person is unknown or the photo has no faces."
        ),
        returns="A list of {face_id, score (0.0–1.0)}.",
        example="scores = face_similarity(photo_id, person_id)",
        impl=compare.face_similarity,
        summarize=compare.summarize_face_similarity,
        mutating=False,
    ),
    HostFunction(
        name="match_people",
        signature="match_people(photo_id)",
        description=(
            "Score every face in the photo against all known people -- the inverse "
            "of face_similarity, for identifying who is in a photo."
        ),
        returns=(
            "A list of {face_id, matches: [{person_id, person_name, "
            "score (0.0–1.0)}]}."
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
