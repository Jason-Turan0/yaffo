"""Starlark adapters behind the engine's handler interfaces.

The host calls a hook by evaluating the adapter module followed by one call,

    <adapter code>
    ADAPTER["hooks"]["list_page"](__args__[0], __args__[1])

in a fresh disposable evaluator (run_starlark), so no module state survives between
calls: anything that must persist goes through `ctx["state"]` in and `state_patch`
out. Limits apply per call; time spent in web calls doesn't count against the
deadline (they have their own timeouts). The evaluator sees only the `adapter`
profile below: no data_query, no Yaffo mutations. An adapter's only effect on the
world is the web calls it's granted, and its only effect on Yaffo the data it
returns. See docs/development/automation-sync-adapters.md (Execution model).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from yaffo.background_tasks.automation_sandbox.starlark_runner import RunLimits, run_starlark
from yaffo.common import MEDIA_EXTENSIONS
from yaffo.sync.convert import Accepts
from yaffo.sync.handlers import ERROR_CODES, ERROR_CHANGED, HookContext, SourceHandler, TargetCapabilities, TargetHandler, PRESENT, MISSING
from yaffo.sync.web import CONNECTION_TOKEN, DownloadError, WebClient, hex_from_base64, url_template

HOOK_LIMITS = RunLimits(timeout_seconds=60.0, max_host_calls=1000)
UNTIMED = frozenset({"web_request", "web_upload_media"})
MAX_RECONCILE_PAGES = 1000

_ENVELOPE_KEYS = {
    "list_page": {"records", "next_cursor", "checkpoint", "state_patch", "error"},
    "resolve_download": {"download", "error"},
    "choices": {"items", "next_cursor", "error"},
    "push": {"results", "state_patch", "error"},
    "remove": {"results", "state_patch", "error"},
    "update": {"results", "state_patch", "error"},
    "list_remote": {"items", "next_cursor", "state_patch", "error"},
}


@dataclass(frozen=True)
class AdapterHostFunction:
    """One function in the `adapter` profile (documented for the future builder)."""
    name: str
    signature: str
    description: str


ADAPTER_HOST_API: tuple[AdapterHostFunction, ...] = (
    AdapterHostFunction(
        "web_request", "web_request(connection, method, url, json=None, headers=None, query=None)",
        "An authorized request through the sync's account. `connection` is always CONNECTION; the method "
        "and URL must be literals, module constants, or url_template(...) so the call can be granted. "
        'Returns {"status", "json", "text", "headers"}, or {"status": 0, "error"} when it\'s denied or fails.'),
    AdapterHostFunction(
        "url_template", "url_template(template, **values)",
        "Fill `{placeholders}` in a literal URL template, encoding each value; `{path}` keeps slashes."),
    AdapterHostFunction(
        "web_upload_media", "web_upload_media(connection, method, url, media_item_id, encoding='raw', "
        "field=None, fields=None, headers=None, offset=0, length=None)",
        "Upload a prepared batch item's bytes. A bounded offset and length support upload sessions."),
    AdapterHostFunction(
        "hex_from_base64", "hex_from_base64(text)",
        "A base64 digest as lowercase hex (for a record's hash), or None if it isn't base64."),
    AdapterHostFunction("print", "print(*values)", "Captured into the run's output."),
)


class HookFailed(Exception):
    """The hook raised, timed out, or returned something that isn't an object."""


@dataclass
class HookCall:
    """What a hook needs at run time: the adapter's code and its web access."""
    code: str
    client: WebClient
    should_cancel: Callable[[], bool] | None = None

    def call(self, hook_name: str, hooks: dict[str, str], *args: Any) -> dict[str, Any]:
        if hook_name not in hooks:
            raise HookFailed(f"the adapter has no {hook_name!r} hook")
        program = (f"{self.code}\n\n"
                   f"ADAPTER[\"hooks\"][{hook_name!r}]("
                   + ", ".join(f"__args__[{i}]" for i in range(len(args))) + ")\n")
        functions = {"web_request": self.client.web_request,
                     "web_upload_media": self.client.web_upload_media, "url_template": url_template,
                     "hex_from_base64": hex_from_base64}
        result = run_starlark(program, inputs={"__args__": list(args), "CONNECTION": CONNECTION_TOKEN},
                              functions=functions, filename="adapter.star", limits=HOOK_LIMITS,
                              should_cancel=self.should_cancel, untimed_functions=UNTIMED)
        if not result.success:
            raise HookFailed(f"{hook_name} failed: {result.error}")
        value = result.value
        if not isinstance(value, dict):
            raise HookFailed(f"{hook_name} must return a dict")
        unknown = set(value) - _ENVELOPE_KEYS.get(hook_name, set(value))
        if unknown:
            raise HookFailed(f"{hook_name} returned unknown keys {sorted(unknown)}")
        error = value.get("error")
        if error is not None and (not isinstance(error, dict) or error.get("code") not in (*ERROR_CODES, ERROR_CHANGED)):
            raise HookFailed(f"{hook_name} returned an invalid error {error!r}")
        return value


def remote_label(options: dict[str, Any], metadata: dict[str, Any]) -> str | None:
    """The label of the adapter's first remote option value ("Pictures › Camera Roll")."""
    for key, spec in (metadata.get("options") or {}).items():
        value = options.get(key)
        if spec.get("type", "").startswith("remote_") and isinstance(value, dict):
            return value.get("label")
    return None


class StarlarkSourceHandler(SourceHandler):
    """A source adapter written in Starlark. `attach` binds the sync's web access
    before any hook runs."""

    def __init__(self, code: str, metadata: dict[str, Any], provider_label: str):
        self.code = code
        self.metadata = metadata
        self.hooks: dict[str, str] = dict(metadata.get("hooks") or {})
        self.provider_label = provider_label
        self.hook_call: HookCall | None = None

    def attach(self, client: WebClient, should_cancel: Callable[[], bool] | None = None) -> None:
        self.hook_call = HookCall(self.code, client, should_cancel)

    @property
    def client(self) -> WebClient:
        if self.hook_call is None:
            raise HookFailed("the adapter isn't attached to an account")
        return self.hook_call.client

    def _call(self, hook: str, *args: Any) -> dict[str, Any]:
        if self.hook_call is None:
            raise HookFailed("the adapter isn't attached to an account")
        return self.hook_call.call(hook, self.hooks, *args)

    def describe_source(self, options: dict[str, Any]) -> str:
        label = remote_label(options, self.metadata)
        return f"{self.provider_label} › {label}" if label else self.provider_label

    def list_page(self, cursor: str | None, ctx: HookContext) -> dict[str, Any]:
        self.client.boundary()
        return self._call("list_page", cursor, ctx.to_contract())

    def choices(self, option: str, parent_id: str | None, cursor: str | None, ctx: HookContext) -> dict[str, Any]:
        return self._call("choices", option, parent_id, cursor, ctx.to_contract())

    def fetch(self, record: dict[str, Any], destination: Path, ctx: HookContext) -> None:
        if Path(record.get("filename") or "").suffix.lower() not in MEDIA_EXTENSIONS:
            raise DownloadError(f"{record.get('filename')!r} isn't a photo or video Yaffo can index")
        download = record.get("download")
        if not download and "resolve_download" in self.hooks:
            self.client.mid_page = True
            try:
                resolved = self._call("resolve_download", record, ctx.to_contract())
            except HookFailed as e:
                raise DownloadError(str(e)) from e
            finally:
                self.client.mid_page = False
            if resolved.get("error"):
                raise DownloadError(f"resolve_download: {resolved['error'].get('code')}")
            download = resolved.get("download")
        if not isinstance(download, dict) or not isinstance(download.get("url"), str):
            raise DownloadError("the record has nothing to download")
        size = record.get("size")
        self.client.download(download["url"], download.get("headers"), destination,
                             max_bytes=size + 1024 * 1024 if isinstance(size, int) else None)


class StarlarkTargetHandler(TargetHandler):
    def __init__(self, code: str, metadata: dict[str, Any], provider_label: str):
        self.code = code
        self.metadata = metadata
        self.hooks: dict[str, str] = dict(metadata.get("hooks") or {})
        self.provider_label = provider_label
        self.hook_call: HookCall | None = None

    def attach(self, client: WebClient, should_cancel: Callable[[], bool] | None = None) -> None:
        self.hook_call = HookCall(self.code, client, should_cancel)

    @property
    def client(self) -> WebClient:
        if self.hook_call is None:
            raise HookFailed("the adapter isn't attached to an account")
        return self.hook_call.client

    def _call(self, hook: str, *args: Any) -> dict[str, Any]:
        if self.hook_call is None:
            raise HookFailed("the adapter isn't attached to an account")
        self.client.boundary()
        return self.hook_call.call(hook, self.hooks, *args)

    def capabilities(self, options: dict[str, Any]) -> TargetCapabilities:
        caps = self.metadata.get("capabilities") or {}
        return TargetCapabilities(max_batch=caps.get("max_batch", 50),
                                  max_batch_bytes=caps.get("max_batch_bytes", 200_000_000),
                                  remove=bool(caps.get("remove")), update=bool(caps.get("update")),
                                  reconcile=bool(caps.get("reconcile")), targets=caps.get("targets", "one"),
                                  accepts=Accepts.from_dict(caps.get("accepts")))

    def describe_destination(self, options: dict[str, Any]) -> str:
        label = remote_label(options, self.metadata)
        return f"{self.provider_label} › {label}" if label else self.provider_label

    def choices(self, option: str, parent_id: str | None, cursor: str | None, ctx: HookContext) -> dict[str, Any]:
        return self._call("choices", option, parent_id, cursor, ctx.to_contract())

    def push(self, batch: list[dict[str, Any]], ctx: HookContext) -> dict[str, Any]:
        return self._call("push", batch, ctx.to_contract())

    def remove(self, items: list[dict[str, Any]], ctx: HookContext) -> dict[str, Any]:
        return self._call("remove", items, ctx.to_contract())

    def update(self, items: list[dict[str, Any]], ctx: HookContext) -> dict[str, Any]:
        return self._call("update", items, ctx.to_contract())

    def reconcile(self, items: list[dict[str, Any]], ctx: HookContext) -> dict[str, Any]:
        # The hook pages through the remote collection. For a pending row with no
        # remote id, filename and target provide the recovery match.
        found: dict[str, Any] = {}
        cursor = None
        remote: list[dict] = []
        patch: dict[str, Any] = {}
        contract = ctx.to_contract()
        contract["reconcile_items"] = items
        seen_cursors: set[str] = set()
        for _ in range(MAX_RECONCILE_PAGES):
            page = self._call("list_remote", cursor, contract)
            if page.get("error"):
                raise HookFailed(f"list_remote: {page['error']}")
            entries = page.get("items") or []
            state_patch = page.get("state_patch") or {}
            if not isinstance(entries, list) or not isinstance(state_patch, dict) or any(
                    not isinstance(entry, dict) or not isinstance(entry.get("remote_id"), str)
                    or not isinstance(entry.get("target"), str) for entry in entries):
                raise HookFailed("list_remote returned invalid items or state")
            remote.extend(entries)
            patch.update(state_patch)
            cursor = page.get("next_cursor")
            if not cursor:
                break
            if not isinstance(cursor, str) or cursor in seen_cursors:
                raise HookFailed("list_remote returned a repeated or invalid cursor")
            seen_cursors.add(cursor)
        else:
            raise HookFailed("list_remote exceeded the page limit")
        for item in items:
            match = next((r for r in remote if r.get("target") == item.get("target") and
                          (r.get("remote_id") == item.get("remote_id") if item.get("remote_id") else
                           bool(item.get("filename")) and r.get("filename") == item.get("filename"))), None)
            found[item["key"]] = ({"status": PRESENT, "remote_id": match.get("remote_id"),
                                   "details": match.get("details") or {}} if match else MISSING)
        if patch:
            found["__state_patch__"] = patch
        return found
