"""The words for sync codes: run outcomes and problems (run history), runtime and
pause states (the status line), ledger statuses and item errors (the Items list).
Everything is stored as codes and translated here at render time, so each viewer
reads it in their own language (see the job status-codes convention)."""
from __future__ import annotations

from typing import Any

from flask_babel import gettext, ngettext

from yaffo.db.models import (
    SYNC_ITEM_CANT_REMOVE, SYNC_ITEM_DELETED_AT_SOURCE, SYNC_ITEM_FAILED, SYNC_ITEM_IMPORTED,
    SYNC_ITEM_LINKED_EXISTING, SYNC_ITEM_PENDING, SYNC_ITEM_REMOVED, SYNC_ITEM_REMOVED_REMOTELY, SYNC_ITEM_REMOVING,
    SYNC_ITEM_SENT, SYNC_ITEM_SKIPPED,
)
from yaffo.sync import guards
from yaffo.sync.handlers import (
    ERROR_AUTH, ERROR_CHANGED, ERROR_FATAL, ERROR_NOT_FOUND, ERROR_RATE_LIMITED, ERROR_REJECTED,
)
from yaffo.sync.schemas import (
    LAYOUT_FLAT, LAYOUT_SOURCE_FOLDERS, LAYOUT_YEAR, LAYOUT_YEAR_MONTH, PAUSE_DESTINATION_MISSING, PAUSE_OPTION_MISSING, PAUSE_SELECTION_MISSING, RUNTIME_INVALID_CONFIG,
    RUNTIME_NEEDS_CONSENT, RUNTIME_NEEDS_SIGN_IN, RUNTIME_PAUSED, RUNTIME_READY,
)
from yaffo.sync.source_run import ITEM_IMPORT_FAILED
from yaffo.sync.target_run import (
    ITEM_CONVERSION_FAILED, ITEM_NOT_SENT, ITEM_SOURCE_MISSING, ITEM_UNSUPPORTED_FORMAT,
)
from yaffo.utils import job_codes as codes


def _n(data: dict, key: str) -> int:
    try:
        return int(data.get(key) or 0)
    except (TypeError, ValueError):
        return 0


def _target_outcome(data: dict) -> str:
    folder = bool(_n(data, "folder"))
    parts: list[str] = []
    sent = _n(data, "sent")
    if sent:
        parts.append(ngettext("Wrote %(num)d photo to the folder", "Wrote %(num)d photos to the folder", sent)
                     if folder else ngettext("Sent %(num)d photo", "Sent %(num)d photos", sent))
    removed = _n(data, "removed")
    if removed:
        parts.append(ngettext("removed %(num)d", "removed %(num)d", removed))
    kept = _n(data, "kept")
    if kept:
        parts.append(ngettext("kept %(num)d copy that left the selection",
                              "kept %(num)d copies that left the selection", kept))
    gone = _n(data, "removed_remotely")
    if gone:
        parts.append(ngettext("%(num)d was deleted there and won't be sent again",
                              "%(num)d were deleted there and won't be sent again", gone))
    cant = _n(data, "cant_remove")
    if cant:
        parts.append(ngettext("%(num)d couldn't be removed", "%(num)d couldn't be removed", cant))
    if not parts:
        return gettext("Up to date")
    text = ", ".join(parts)
    return text[0].upper() + text[1:]


def _source_outcome(data: dict) -> str:
    parts: list[str] = []
    imported = _n(data, "imported")
    if imported:
        parts.append(ngettext("Imported %(num)d new photo", "Imported %(num)d new photos", imported))
    linked = _n(data, "linked")
    if linked:
        parts.append(ngettext("%(num)d was already in your library", "%(num)d were already in your library",
                              linked))
    replaced = _n(data, "replaced")
    if replaced:
        parts.append(ngettext("updated %(num)d edited photo", "updated %(num)d edited photos", replaced))
    deleted = _n(data, "deleted_at_source")
    if deleted:
        parts.append(ngettext("%(num)d deleted at the source and kept in your library",
                              "%(num)d deleted at the source and kept in your library", deleted))
    if not parts:
        return gettext("Nothing new")
    text = ", ".join(parts)
    return text[0].upper() + text[1:]


def outcome_sentence(data: dict) -> str | None:
    outcome = data.get("outcome")
    if outcome == codes.OUTCOME_SYNC_SENT:
        return _target_outcome(data)
    if outcome == codes.OUTCOME_SYNC_IMPORTED:
        return _source_outcome(data)
    if outcome == codes.OUTCOME_SYNC_WAITING_FOR_FOLDER:
        return gettext("Waiting for the folder: it isn't connected")
    if outcome == codes.OUTCOME_SYNC_NOT_RUN:
        return gettext("Didn't run")
    return None


def pause_text(pause_code: str | None) -> str:
    if pause_code == PAUSE_SELECTION_MISSING:
        return gettext("The selected album was deleted. Choose another to send.")
    if pause_code == PAUSE_DESTINATION_MISSING:
        return gettext("The destination isn't available (an unplugged drive or a removed folder?). "
                       "The sync carries on when it's back.")
    if pause_code == PAUSE_OPTION_MISSING:
        return gettext("The chosen folder or album no longer exists at the service. Choose another.")
    return gettext("Paused.")


def problem_sentence(code: str | None, params: dict[str, Any]) -> str | None:
    if code == codes.PROBLEM_SYNC_NEEDS_CONSENT:
        return gettext("The setup changed since this sync was turned on. Review it and turn it on again.")
    if code == codes.PROBLEM_SYNC_INVALID_CONFIG:
        return gettext("This sync's setup can't be used. Open it to check the folder and options.")
    if code == codes.PROBLEM_SYNC_PAUSED:
        return pause_text(params.get("pause_code"))
    if code == codes.PROBLEM_SYNC_ITEMS_FAILED:
        count = _n(params, "count")
        return ngettext("%(num)d item didn't go through. See Items for why.",
                        "%(num)d items didn't go through. See Items for why.", count)
    if code == codes.PROBLEM_SYNC_STOPPED:
        stop = params.get("code")
        if stop == ERROR_RATE_LIMITED:
            return gettext("The service asked Yaffo to slow down. The next run carries on.")
        if stop == ERROR_AUTH:
            return gettext("The account needs to sign in again.")
        if stop == ERROR_FATAL:
            return gettext("The run stopped because of an error in the service's code.")
        if stop == ERROR_NOT_FOUND:
            return gettext("Something the sync reads from is gone at the service. It tries again later.")
        return gettext("The run stopped early. The next run carries on.")
    if code == codes.PROBLEM_SYNC_NEEDS_SIGN_IN:
        if params.get("reason") == "needs_scopes":
            return gettext("The account needs to allow more access. Open the sync and sign in again.")
        if params.get("reason") == "account_missing":
            return gettext("The sync has no account. Open it and sign in.")
        return gettext("The account needs to sign in again. Open the sync and sign in.")
    if code == codes.PROBLEM_SYNC_DISK_FULL:
        return gettext("There isn't enough free space left to keep copying.")
    return None


def runtime_text(status: str, pause_code: str | None, enabled: bool) -> str:
    if status == RUNTIME_NEEDS_CONSENT:
        return gettext("Finish the setup below, then turn it on.")
    if status == RUNTIME_PAUSED:
        return pause_text(pause_code)
    if status == RUNTIME_INVALID_CONFIG:
        return gettext("This sync's setup can't be used. Check the folder and options.")
    if status == RUNTIME_NEEDS_SIGN_IN:
        return gettext("Sign in again to continue.")
    if status == RUNTIME_READY and not enabled:
        return gettext("Turned off.")
    return gettext("On.")


def item_status_label(status: str, folder_target: bool = False) -> str:
    return {
        SYNC_ITEM_IMPORTED: gettext("Imported"),
        SYNC_ITEM_LINKED_EXISTING: gettext("Already in library"),
        SYNC_ITEM_SKIPPED: gettext("Skipped"),
        SYNC_ITEM_DELETED_AT_SOURCE: gettext("Deleted at source"),
        SYNC_ITEM_PENDING: gettext("Sending"),
        SYNC_ITEM_SENT: gettext("Written to folder") if folder_target else gettext("Sent"),
        SYNC_ITEM_FAILED: gettext("Failed"),
        SYNC_ITEM_REMOVING: gettext("Removing"),
        SYNC_ITEM_REMOVED: gettext("Removed"),
        SYNC_ITEM_REMOVED_REMOTELY: gettext("Deleted there"),
        SYNC_ITEM_CANT_REMOVE: gettext("Can't remove"),
    }.get(status, status)


def item_error_text(error_code: str | None) -> str | None:
    if not error_code:
        return None
    return {
        ITEM_NOT_SENT: gettext("Not attempted yet; tried again on the next run."),
        ITEM_SOURCE_MISSING: gettext("The photo's file is missing."),
        ITEM_UNSUPPORTED_FORMAT: gettext("This target doesn't take this kind of file."),
        ITEM_CONVERSION_FAILED: gettext("Couldn't convert it to JPEG."),
        ITEM_IMPORT_FAILED: gettext("Couldn't copy the file."),
        ERROR_CHANGED: gettext("The copy was changed after Yaffo wrote it, so Yaffo left it alone."),
        ERROR_REJECTED: gettext("The service refused it."),
    }.get(error_code, error_code)


def guard_text(problem: str | None) -> str | None:
    if problem is None:
        return None
    return {
        guards.PROBLEM_INSIDE_LIBRARY: gettext("That folder is inside your library. Choose one outside it."),
        guards.PROBLEM_CONTAINS_LIBRARY: gettext("That folder contains your library. Choose a smaller one."),
        guards.PROBLEM_INSIDE_THUMBNAILS: gettext("That folder is Yaffo's thumbnail folder. Choose another."),
        guards.PROBLEM_INSIDE_IMPORT_ROOT: gettext("That folder is where a sync imports to. Choose another."),
        guards.PROBLEM_IMPORT_ROOT_OUTSIDE: gettext("The import folder must be inside the media folder."),
        guards.PROBLEM_UNKNOWN_MEDIA_DIR: gettext("Choose one of your media folders."),
        guards.PROBLEM_MEDIA_DIR_MISSING: gettext("That media folder isn't connected."),
    }.get(problem, problem)


def import_layout_choices() -> list[dict[str, str]]:
    """The "Arrange by" choices for an import's new files, in the order offered."""
    return [
        {"value": LAYOUT_SOURCE_FOLDERS, "label": gettext("Keep the source's folders")},
        {"value": LAYOUT_YEAR_MONTH, "label": gettext("By year and month taken")},
        {"value": LAYOUT_YEAR, "label": gettext("By year taken")},
        {"value": LAYOUT_FLAT, "label": gettext("All in one folder")},
    ]


def import_layout_label(layout: str | None) -> str:
    return next((c["label"] for c in import_layout_choices() if c["value"] == layout), layout or "")


def import_into_text(root: str, layout: str | None) -> str:
    """The review's "Into" line: the folder, and how new files are arranged in it."""
    if layout == LAYOUT_SOURCE_FOLDERS:
        return gettext("%(root)s, in the same folders as at the source", root=root)
    if layout == LAYOUT_YEAR:
        return gettext("%(root)s, in a folder per year taken", root=root)
    if layout == LAYOUT_FLAT:
        return gettext("%(root)s, all in one folder", root=root)
    return gettext("%(root)s, in a folder per year and month taken", root=root)
