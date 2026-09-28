"""Stable codes a Job records about how its run went, and the helper that writes
them.

A Job never stores text a user reads. It stores an outcome code (how the run
ended) and, when the run needs attention, a problem code, each with the numbers
or paths its sentence needs, in `job_data`. The run history and job cards
translate them at render time (routes/utilities/run_history.py). English
diagnostics stay in `Job.error`, and tuning values in `job_data["details"]`;
both are shown only under a row's Details and passed to the assistant.

File sync's outcome codes live with it (utils/file_sync.py, SYNC_*).
"""
import json
from typing import Any

# ---- outcomes of the system automations (recorded by automation_runs.record_run)
OUTCOME_NO_MEDIA = "no_media"          # the scope held no indexed media
OUTCOME_LABELED = "labeled"            # classify_labels: {labeled, total}
OUTCOME_ASSIGNED = "assigned"          # auto_assign_faces: {faces, photos}
OUTCOME_NAMED = "named"                # assign_location_name: {named, total}
OUTCOME_GEOTAGGED = "geotagged"        # geotag_from_neighbors: {geotagged, total}
OUTCOME_WRITTEN = "written"            # export_photo_tag: {written, total}
# ---- outcomes of utility jobs
OUTCOME_REGENERATED = "regenerated"    # regenerate_thumbnails: {written, total}

# ---- problems: why a run needs attention
PROBLEM_MEDIA_FOLDER_EMPTY = "media_folder_empty"  # file sync held items back: {roots, count}
PROBLEM_ITEMS_UNPROCESSED = "items_unprocessed"    # a crashed batch never reported: {count}
PROBLEM_WORKER_STOPPED = "worker_stopped"          # the task's worker crashed
PROBLEM_TASK_ERROR = "task_error"                  # the task raised
PROBLEM_SCRIPT_ERROR = "script_error"              # a custom script failed
PROBLEM_SCRIPT_TIMEOUT = "script_timeout"          # ... ran past the sandbox time limit
PROBLEM_SCRIPT_CALL_LIMIT = "script_call_limit"    # ... made too many host calls
# Dispatch failures keep their original key, job_data["dispatch_error_code"].
PROBLEM_INVALID_SCOPE = "invalid_scope"
PROBLEM_DISPATCH_FAILED = "dispatch_failed"
PROBLEM_EVENT_DISPATCH_FAILED = "event_dispatch_failed"


def load_job_data(raw: str | None) -> dict[str, Any]:
    """A Job's job_data as a dict ({} when empty or not a JSON object)."""
    try:
        data = json.loads(raw or "{}")
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def with_problem(raw: str | None, code: str, **params: Any) -> str:
    """`raw` job_data with a problem recorded on it, keeping everything else."""
    data = load_job_data(raw)
    data["problem"] = code
    if params:
        data["problem_params"] = params
    return json.dumps(data)
