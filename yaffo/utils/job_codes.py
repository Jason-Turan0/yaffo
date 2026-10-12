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
# ---- outcomes of sync automations (yaffo/sync/engine.py)
OUTCOME_SYNC_SENT = "sync_sent"                    # target: {sent, removed, kept, failed, cant_remove,
                                                   #   unsupported, converted, unchanged, written_to_folder}
OUTCOME_SYNC_IMPORTED = "sync_imported"            # source: {imported, linked, replaced, unchanged, failed}
OUTCOME_SYNC_WAITING_FOR_FOLDER = "sync_waiting_for_folder"  # Folder source: the folder isn't connected
OUTCOME_SYNC_NOT_RUN = "sync_not_run"              # the sync needs something first (see problem)
OUTCOME_SYNC_WAITING_FOR_DEVICE = "sync_waiting_for_device"  # peer source: {device}: offline on hub and LAN
OUTCOME_SYNC_WAITING_FOR_DIRECT = "sync_waiting_for_direct"  # peer source: {device}: only a relay path, relay off
OUTCOME_SYNC_RELAY_BUDGET = "sync_relay_budget"              # peer source: {device}: this run's relay budget used
OUTCOME_SYNC_SHARING_OFF = "sync_sharing_off"                # peer source: device sharing isn't running

# ---- problems: why a run needs attention
PROBLEM_MEDIA_FOLDER_EMPTY = "media_folder_empty"  # file sync held items back: {roots, count}
PROBLEM_ITEMS_UNPROCESSED = "items_unprocessed"    # a crashed batch never reported: {count}
PROBLEM_WORKER_STOPPED = "worker_stopped"          # the task's worker crashed
PROBLEM_TASK_ERROR = "task_error"                  # the task raised
PROBLEM_SCRIPT_ERROR = "script_error"              # a custom script failed
PROBLEM_SCRIPT_TIMEOUT = "script_timeout"          # ... ran past the sandbox time limit
PROBLEM_SCRIPT_CALL_LIMIT = "script_call_limit"    # ... made too many host calls
PROBLEM_SYNC_NEEDS_CONSENT = "sync_needs_consent"  # the setup changed since it was turned on
PROBLEM_SYNC_INVALID_CONFIG = "sync_invalid_config"  # stored config or adapter can't be used
PROBLEM_SYNC_PAUSED = "sync_paused"                # {pause_code}: selection/destination missing
PROBLEM_SYNC_ITEMS_FAILED = "sync_items_failed"    # {count}: some items failed this run
PROBLEM_SYNC_ITEMS_REFUSED = "sync_items_refused"  # {count}: items the service refused before weren't retried
PROBLEM_SYNC_STOPPED = "sync_stopped"              # {code}: the service stopped the run (rate limit, ...)
PROBLEM_SYNC_DISK_FULL = "sync_disk_full"          # not enough free space to keep copying
PROBLEM_SYNC_INTERRUPTED = "sync_interrupted"      # {outcome, ...}: the source went away mid-run (a peer outcome)
PROBLEM_SYNC_NEEDS_SIGN_IN = "sync_needs_sign_in"  # {reason}: the account must sign in (again)
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


def with_problem(raw: str | None, code: str, /, **params: Any) -> str:
    """`raw` job_data with a problem recorded on it, keeping everything else."""
    data = load_job_data(raw)
    data["problem"] = code
    if params:
        data["problem_params"] = params
    return json.dumps(data)
