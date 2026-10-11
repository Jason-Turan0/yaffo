"""Built-in source: copy new photos from a paired Yaffo device (docs/development/peer-sync.md).

"Copy new photos from *Mom's laptop › Vacation 2026* into *Pictures › Imports › Mom*."
The other side is a scope the device has granted to this one: a media folder, a
folder in it, or an album. The serving device re-decides every request against its
grants, so nothing here can reach past what it shares.

Sync runs execute in task workers, which can't run the P2P engine (it owns this
device's hub socket, UDP port, and mDNS name), so every peer call goes through the
web process's internal API (yaffo/sync/peer_client.py).

- Records: `source_id` is `media_dir_id:relative_path` on the peer (stable when it
  re-indexes; its media item ids aren't), `updated_at` is `size:mtime`, and the
  manifest's `sha1` (protocol 2) lets the engine link a photo already in the library
  without downloading it.
- No tombstones: Yaffo can't tell "deleted" from "no longer shared", and the
  library is the master copy either way.
- Downloads resume: the engine names each record's temporary file after the record
  (`resumable_fetch`), and the web process keeps the partial between runs.
- One session per run, opened on the first download (a run with nothing new never
  connects), with up to READ_AHEAD more downloads started on it while the engine
  handles the current one.
"""
from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import Any, Callable

from flask_babel import gettext

from yaffo.sync.handlers import (
    ERROR_FATAL, ChoicesUnavailable, HookContext, SourceHandler, SourceItemSkipped, SourceStop, STOP_OUT_OF_TIME,
)
from yaffo.sync.options import OPTION_REMOTE_COLLECTION
from yaffo.sync.peer_client import PeerClient, PeerClientError
from yaffo.sync.schemas import PAUSE_DEVICE_REVOKED, PAUSE_OPTION_MISSING
from yaffo.utils import job_codes as codes

PAGE_SIZE = 200
# Downloads started ahead of the one the engine is waiting for (TransferManager
# pulls three files at a time on its connection; so does a peer sync).
READ_AHEAD = 2
POLL_SECONDS = 10
MAX_SESSION_RECOVERIES = 2

ITEM_FILE_REFUSED = "file_refused"

OPTIONS: dict[str, dict[str, Any]] = {
    "device": {"type": OPTION_REMOTE_COLLECTION, "label": "Device", "required": True,
               "help": "A device paired with this one on the Sharing page."},
    "scope": {"type": OPTION_REMOTE_COLLECTION, "label": "Shared folder or album", "needed": True,
              "depends_on": "device",
              "help": "A folder or album the device shares with this one."},
}

# Internal API error codes, and what they mean for the run.
_RUN_OUTCOMES = {
    "peer_offline": codes.OUTCOME_SYNC_WAITING_FOR_DEVICE,
    "relay_not_allowed": codes.OUTCOME_SYNC_WAITING_FOR_DIRECT,
    "engine_unavailable": codes.OUTCOME_SYNC_SHARING_OFF,
}
_RUN_PAUSES = {
    "not_trusted": PAUSE_DEVICE_REVOKED,
    "scope_not_shared": PAUSE_OPTION_MISSING,
}
# One file failed in a way the next run may get past: the engine marks it failed and
# a later full listing tries it again.
_RETRY_ITEM = {"source_changed", "checksum_mismatch"}


def _value_id(value: Any) -> str | None:
    return value.get("id") if isinstance(value, dict) else None


def _value_label(value: Any) -> str:
    return (value.get("label") if isinstance(value, dict) else None) or ""


def _scope_folder(scope_key: str) -> str:
    """The folder a scope covers on the peer, relative to its media folder ("" for a
    whole media folder or an album)."""
    kind, _, rest = scope_key.partition(":")
    if kind == "folder":
        return rest.partition(":")[2]
    return ""


def record_of(manifest: dict, scope_key: str) -> dict:
    relative = PurePosixPath(manifest.get("relative_path") or manifest.get("name") or "")
    scope_folder = _scope_folder(scope_key)
    parent = relative.parent
    if scope_folder:
        try:
            parent = parent.relative_to(scope_folder)
        except ValueError:
            pass
    size = int(manifest.get("size") or 0)
    mtime = float(manifest.get("mtime") or 0)
    record: dict[str, Any] = {
        "source_id": manifest["source_id"],
        "filename": manifest.get("name") or relative.name,
        "folder": "" if str(parent) == "." else parent.as_posix(),
        "size": size,
        "updated_at": f"{size}:{mtime}",
        "peer": {"media_item_id": int(manifest["media_item_id"]), "mtime": mtime},
    }
    if manifest.get("sha1"):
        record["hash"] = {"algo": "sha1", "value": manifest["sha1"]}
    if isinstance(manifest.get("date_taken"), str) and manifest["date_taken"]:
        record["taken_at"] = {"local": manifest["date_taken"]}
    return record


def scope_label(scope: dict) -> str:
    if scope.get("scope_type") == "album":
        return scope.get("name") or ""
    if scope.get("relative_path"):
        return f"{scope.get('name')} › {scope['relative_path']}"
    return scope.get("name") or ""


class PeerSource(SourceHandler):
    resumable_fetch = True

    def __init__(self, client_factory: Callable[[], PeerClient] = PeerClient) -> None:
        self._client_factory = client_factory
        self._client: PeerClient | None = None
        self._devices: list[dict] | None = None
        self._session: dict | None = None
        self._owner: str | None = None
        # source_id -> (pull_id, destination) for downloads this run started.
        self._pulls: dict[str, tuple[str, str]] = {}
        self._plan: list[tuple[dict, Path]] = []

    @property
    def client(self) -> PeerClient:
        if self._client is None:
            self._client = self._client_factory()
        return self._client

    # ---- description and pickers ----------------------------------------------------

    def describe_source(self, options: dict[str, Any]) -> str:
        device, scope = _value_label(options.get("device")), _value_label(options.get("scope"))
        return f"{device} › {scope}" if device and scope else device or scope

    def choices(self, option: str, parent: str | None, cursor: str | None, options: dict[str, Any]) -> dict:
        try:
            if option == "device":
                devices = [d for d in self.client.devices() if d.get("trust_state") == "trusted"]
                if not devices:
                    raise ChoicesUnavailable(gettext("No paired devices yet. Pair one on the Sharing page first."))
                return {"items": [{"id": d["device_id"], "label": d.get("display_name") or d["device_id"]}
                                  for d in devices], "next_cursor": None}
            if option == "scope":
                device_id = _value_id(options.get("device"))
                if not device_id:
                    raise ChoicesUnavailable(gettext("Choose a device first."))
                scopes = self.client.scopes(device_id).get("scopes") or []
                if not scopes:
                    raise ChoicesUnavailable(gettext("This device doesn't share anything with this one yet."))
                return {"items": [{"id": s["scope_key"], "label": scope_label(s)} for s in scopes],
                        "next_cursor": None}
        except PeerClientError as e:
            if e.code == "engine_unavailable":
                raise ChoicesUnavailable(gettext("Device sharing is off.")) from e
            if e.code == "not_trusted":
                raise ChoicesUnavailable(gettext("This device isn't paired with this one any more.")) from e
            if e.code == "peer_offline":
                raise ChoicesUnavailable(gettext("The device isn't reachable. Open Yaffo on it and try again.")) from e
            raise ChoicesUnavailable(gettext("Couldn't ask the device. Try again.")) from e
        raise ChoicesUnavailable(gettext("Nothing to choose here."))

    # ---- run start ---------------------------------------------------------------

    def _device(self, options: dict[str, Any]) -> dict | None:
        if self._devices is None:
            self._devices = self.client.devices()
        device_id = _value_id(options.get("device"))
        return next((d for d in self._devices if d.get("device_id") == device_id), None)

    def source_pause(self, options: dict[str, Any]) -> str | None:
        try:
            device = self._device(options)
        except PeerClientError:
            return None  # source_problem says what's wrong
        if device is None or device.get("trust_state") != "trusted":
            return PAUSE_DEVICE_REVOKED
        return None

    def source_problem(self, options: dict[str, Any]) -> str | None:
        try:
            if not self.client.status().get("available"):
                return codes.OUTCOME_SYNC_SHARING_OFF
            device = self._device(options)
        except PeerClientError as e:
            return _RUN_OUTCOMES.get(e.code, codes.OUTCOME_SYNC_WAITING_FOR_DEVICE)
        # Offline on the hub and not on the LAN. Unknown presence (hub unreachable,
        # online is null) is worth a try: the LAN path may still work.
        if device is not None and device.get("online") is False and not device.get("local"):
            return codes.OUTCOME_SYNC_WAITING_FOR_DEVICE
        return None

    def summary_params(self) -> dict[str, Any]:
        params: dict[str, Any] = {}
        if self._session is not None and self._session.get("path"):
            params["path"] = self._session["path"]
        return params

    # ---- errors --------------------------------------------------------------------

    def _device_name(self, options: dict[str, Any]) -> str:
        """The device's current name when this run has already listed the devices, else
        the name it had when it was chosen."""
        device_id = _value_id(options.get("device"))
        known = next((d for d in self._devices or [] if d.get("device_id") == device_id), None)
        return (known or {}).get("display_name") or _value_label(options.get("device"))

    def _stop(self, error: PeerClientError, options: dict[str, Any]) -> SourceStop:
        device = self._device_name(options)
        if error.code in _RUN_PAUSES:
            return SourceStop(error.message, pause_code=_RUN_PAUSES[error.code])
        if error.code in _RUN_OUTCOMES:
            return SourceStop(error.message, outcome=_RUN_OUTCOMES[error.code], params={"device": device})
        if error.code == "session_expired":
            return SourceStop(error.message, outcome=codes.OUTCOME_SYNC_WAITING_FOR_DEVICE, params={"device": device})
        return SourceStop(error.message, error_code=ERROR_FATAL)

    # ---- listing -------------------------------------------------------------------

    def list_page(self, cursor: str | None, ctx: HookContext) -> dict[str, Any]:
        options = ctx.options
        scope_key = _value_id(options.get("scope")) or ""
        session_id = self._session["session_id"] if self._session else None
        try:
            page = self.client.files(_value_id(options.get("device")) or "", scope_key, cursor, PAGE_SIZE,
                                     session_id=session_id, filters=ctx.filters)
        except PeerClientError as e:
            if e.code == "session_expired" and session_id:
                self._session = None
                return self.list_page(cursor, ctx)
            raise self._stop(e, options) from e
        records = [record_of(manifest, scope_key) for manifest in page.get("files") or []]
        total = page.get("total")
        next_cursor = page.get("next_cursor")
        progress = None
        if isinstance(total, int) and total >= 0:
            progress = {"done": min(int(next_cursor), total) if next_cursor else total, "total": total}
        return {"records": records, "next_cursor": next_cursor, "progress": progress}

    # ---- downloading -----------------------------------------------------------------

    def _ensure_session(self, ctx: HookContext) -> str:
        if self._session is None:
            self._owner = f"sync-{ctx.automation_id}"
            try:
                self._session = self.client.open_session(_value_id(ctx.options.get("device")) or "", self._owner,
                                                         relay="never")
            except PeerClientError as e:
                raise self._stop(e, ctx.options) from e
        return self._session["session_id"]

    def plan_fetches(self, upcoming: list[tuple[dict, Path]], ctx: HookContext) -> None:
        self._plan = list(upcoming)

    def _start(self, record: dict, destination: Path, ctx: HookContext) -> str:
        known = self._pulls.get(record["source_id"])
        if known is not None and known[1] == str(destination):
            return known[0]
        peer = record["peer"]
        for attempt in range(MAX_SESSION_RECOVERIES + 1):
            session_id = self._ensure_session(ctx)
            try:
                pull_id = self.client.start_pull(session_id, peer["media_item_id"], record["source_id"],
                                                 record.get("size") or 0, peer.get("mtime") or 0, str(destination))
                break
            except PeerClientError as e:
                if e.code == "session_expired" and attempt < MAX_SESSION_RECOVERIES:
                    self._session = None
                    continue
                raise self._stop(e, ctx.options) from e
        self._pulls[record["source_id"]] = (pull_id, str(destination))
        return pull_id

    def _read_ahead(self, record: dict, ctx: HookContext) -> None:
        ids = [planned["source_id"] for planned, _ in self._plan]
        if record["source_id"] not in ids:
            return
        index = ids.index(record["source_id"])
        for planned, destination in self._plan[index + 1:index + 1 + READ_AHEAD]:
            try:
                self._start(planned, destination, ctx)
            except SourceStop:
                return  # the engine meets the same problem when it reaches that record

    def fetch(self, record: dict[str, Any], destination: Path, ctx: HookContext) -> None:
        pull_id = self._start(record, destination, ctx)
        self._read_ahead(record, ctx)
        should_stop = ctx.extra.get("should_stop") or (lambda: False)
        recoveries = 0
        while True:
            try:
                status = self.client.pull(pull_id, wait=POLL_SECONDS)
            except PeerClientError as e:
                self._pulls.pop(record["source_id"], None)
                if e.code == "session_expired" and recoveries < MAX_SESSION_RECOVERIES:
                    # The web process restarted: start again on a new session; the
                    # partial on disk makes it a resume.
                    recoveries += 1
                    self._session = None
                    pull_id = self._start(record, destination, ctx)
                    continue
                raise self._stop(e, ctx.options) from e
            state = status.get("state")
            if state == "done":
                self._pulls.pop(record["source_id"], None)
                if not destination.is_file():
                    raise OSError(f"the download of {record['source_id']} didn't arrive")
                return
            if state in ("failed", "cancelled"):
                self._pulls.pop(record["source_id"], None)
                error = status.get("error") or {}
                code = error.get("code") or "cancelled"
                message = error.get("message") or code
                if code == "file_refused":
                    raise SourceItemSkipped(message, error_code=ITEM_FILE_REFUSED)
                if code in _RETRY_ITEM or code == "cancelled":
                    raise OSError(message)
                raise self._stop(PeerClientError(code, message), ctx.options)
            if should_stop():
                # Out of time: stop this download; its partial stays for the next run.
                self._cancel(pull_id)
                self._pulls.pop(record["source_id"], None)
                raise SourceStop("the run's time is up", error_code=STOP_OUT_OF_TIME)

    def _cancel(self, pull_id: str) -> None:
        try:
            self.client.cancel_pull(pull_id)
        except PeerClientError:
            pass

    def finish_run(self, ctx: HookContext) -> None:
        """Cancel downloads started ahead that the run never reached (their partials
        are resumed next time) and let go of the session."""
        for pull_id, _ in self._pulls.values():
            self._cancel(pull_id)
        self._pulls.clear()
        self._plan = []
        if self._session is not None:
            try:
                self.client.close_session(self._session["session_id"], self._owner)
            except PeerClientError:
                pass
