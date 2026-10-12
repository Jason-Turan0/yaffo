"""The service builder's Test: run a custom adapter's draft without changing anything.

The draft's hooks run once each, as a sync would call them first: the option pickers
(`choices`), then a source's first page of records, or a target's push of a few
library photos and its first `list_remote` page. Every endpoint the static check
found counts as granted (it's the user's own draft), but:

- **reads** are sent for real only when the run may read live: the user clicked
  Test (their click is the approval), and the adapter has a signed-in test account.
  Otherwise they're recorded and answered with an explanation;
- **writes and uploads are never sent.** They're recorded and answered with a
  placeholder id (`$ref:N`), as an automation's Test records its changes.

The report says what each hook returned (records checked like a real run's), and
lists every call with its risk and whether it went out. `model_text()` is the same,
trimmed, for the builder model: long fields cut, at most a few records.
"""
from __future__ import annotations

import json
import mimetypes
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from yaffo.db.models import MediaItem, SyncAdapter, SYNC_ROLE_SOURCE
from yaffo.sync import access, connections, credentials
from yaffo.sync.adapters import AdapterUnavailable, LoadedAdapter, load_working
from yaffo.sync.handlers import HookContext
from yaffo.sync.options import OPTION_REMOTE_COLLECTION, OPTION_REMOTE_FOLDER
from yaffo.sync.providers import bind_instance
from yaffo.sync.schemas import CONNECTION_READY, GRANT_ENDPOINT, GRANT_FOLLOWUP, PASS_INCREMENTAL, RISK_READ
from yaffo.sync.source_run import HookOutputError, validate_record
from yaffo.sync.starlark_adapter import HookFailed
from yaffo.sync.web import CONNECTION_TOKEN, WebClient
from yaffo.sync.adapter_static import pattern_regex

# Where a self-hosted service's URLs point in a Test with no test account.
PLACEHOLDER_SERVER = "https://your-server.example"
SAMPLE_PHOTOS = 3
MODEL_RECORDS = 3
MODEL_FIELD_CHARS = 200
MODEL_TEXT_CHARS = 6000
CALL_LIVE, CALL_RECORDED, CALL_DENIED = "live", "recorded", "denied"


@dataclass
class TestCall:
    method: str
    url: str
    risk: str
    mode: str
    status: int | None = None


@dataclass
class AdapterTestReport:
    role: str | None = None
    live: bool = False
    problems: list[str] = field(default_factory=list)
    choices: dict[str, list[dict]] = field(default_factory=dict)       # option -> [{id, label}]
    records: list[dict] = field(default_factory=list)
    record_count: int = 0
    more: bool = False
    results: list[dict] = field(default_factory=list)
    remote: list[dict] = field(default_factory=list)
    calls: list[TestCall] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems

    def to_dict(self) -> dict:
        return {**asdict(self), "ok": self.ok}

    def model_text(self) -> str:
        lines = [f"Test of the draft ({'live reads' if self.live else 'reads recorded, not sent'}): "
                 f"{'passed' if self.ok else 'problems found'}."]
        lines += [f"Problem: {p}" for p in self.problems]
        for option, items in self.choices.items():
            lines.append(f"choices({option!r}): {[(i['id'], i['label']) for i in items[:8]]}")
        if self.role == SYNC_ROLE_SOURCE:
            lines.append(f"list_page returned {self.record_count} record(s); next_cursor {'set' if self.more else 'none'}.")
            lines += [f"record: {_trim(r)}" for r in self.records[:MODEL_RECORDS]]
        elif self.role:
            lines.append(f"push results: {_trim(self.results)}")
            if self.remote:
                lines.append(f"list_remote first page: {_trim(self.remote[:MODEL_RECORDS])}")
        if any(c.mode == CALL_RECORDED and c.risk != RISK_READ for c in self.calls):
            lines.append("Recorded writes answer {\"id\": \"$ref:N\"} instead of the service's reply: a hook "
                         "that fails on that placeholder's shape isn't necessarily wrong.")
        lines.append("Calls:")
        lines += [f"- {c.method} {c.url} [{c.risk}, {c.mode}{f', {c.status}' if c.status is not None else ''}]"
                  for c in self.calls] or ["- none"]
        return "\n".join(lines)[:MODEL_TEXT_CHARS]


def _trim(value: Any) -> str:
    def cut(v: Any) -> Any:
        if isinstance(v, str):
            return v if len(v) <= MODEL_FIELD_CHARS else v[:MODEL_FIELD_CHARS] + "…"
        if isinstance(v, dict):
            return {k: cut(x) for k, x in v.items()}
        if isinstance(v, list):
            return [cut(x) for x in v[:10]]
        return v
    return json.dumps(cut(value), default=str)


class TestWebClient(WebClient):
    """A WebClient that sends reads only when live and never sends a write."""

    def __init__(self, *args: Any, live_reads: bool = False, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self.live_reads = live_reads
        self.log: list[TestCall] = []
        self._refs = 0

    def _risk(self, method: str, url: str) -> str:
        bare = url.split("?", 1)[0]
        for grant in self._valid_grants():
            if grant.method != method:
                continue
            if grant.grant_kind == GRANT_ENDPOINT and grant.url_pattern \
                    and pattern_regex(self.expand(grant.url_pattern)).match(bare):
                return grant.risk
        followups = [g for g in self._valid_grants() if g.method == method and g.grant_kind == GRANT_FOLLOWUP]
        return followups[0].risk if followups else "write"

    def _placeholder(self, method: str, url: str, risk: str) -> dict[str, Any]:
        self._refs += 1
        self.log.append(TestCall(method, url.split("?", 1)[0], risk, CALL_RECORDED))
        if risk == RISK_READ:
            return {"status": 0, "error": "not sent: this Test doesn't read live (no test account, or the "
                                          "builder ran it); the user's Test button reads live"}
        return {"status": 200, "json": {"id": f"$ref:{self._refs}"}, "text": None, "headers": {}}

    def web_request(self, connection: Any, method: str, url: str, json: Any = None, headers: dict | None = None,
                    query: dict | None = None) -> dict[str, Any]:
        if connection != CONNECTION_TOKEN or not isinstance(method, str) or not isinstance(url, str):
            return super().web_request(connection, method, url, json=json, headers=headers, query=query)
        method, full = method.upper(), self.expand(url)
        problem = self.authorize(method, full)
        if problem:
            self.log.append(TestCall(method, full.split("?", 1)[0], "", CALL_DENIED))
            return {"status": 0, "error": f"denied: {problem}"}
        risk = self._risk(method, full)
        if risk != RISK_READ or not self.live_reads:
            return self._placeholder(method, full, risk)
        result = super().web_request(connection, method, url, json=json, headers=headers, query=query)
        self.log.append(TestCall(method, full.split("?", 1)[0], risk, CALL_LIVE, result.get("status")))
        return result

    def web_upload_media(self, connection: Any, method: str, url: str, media_item_id: int,
                         **kwargs: Any) -> dict[str, Any]:
        method, full = str(method).upper(), self.expand(str(url))
        problem = self.authorize(method, full)
        if problem:
            self.log.append(TestCall(method, full.split("?", 1)[0], "", CALL_DENIED))
            return {"status": 0, "error": f"denied: {problem}"}
        return self._placeholder(method, full, "write")


def _sample_batch(session: Session) -> list[dict]:
    """A few recent library photos, as a push batch."""
    items = (session.query(MediaItem).filter(MediaItem.full_file_path.isnot(None))
             .order_by(MediaItem.id.desc()).limit(SAMPLE_PHOTOS * 4).all())
    batch = []
    for item in items:
        path = Path(item.full_file_path)
        if not path.is_file():
            continue
        entry = {"key": str(len(batch) + 1), "media_item_id": item.id, "target": "Yaffo test", "caption": None,
                 "filename": path.name, "mime": mimetypes.guess_type(path.name)[0] or "application/octet-stream",
                 "size": path.stat().st_size}
        if item.date_taken:
            entry["taken_at"] = {"local": str(item.date_taken)[:19]}
        batch.append(entry)
        if len(batch) == SAMPLE_PHOTOS:
            break
    return batch


def _client(session: Session, row: SyncAdapter, adapter: LoadedAdapter, live: bool) -> TestWebClient:
    assert adapter.preset is not None and adapter.published_code_hash is not None
    http = access.make_http()
    connection = connections.get(session, row.test_connection_key) if row.test_connection_key else None
    usable = connection is not None and connection.status == CONNECTION_READY \
        and connection.provider == adapter.provider
    instance = connection.instance if usable else {}
    base_url = instance.get("base_url") or (PLACEHOLDER_SERVER if adapter.preset.instance else None)
    provider = bind_instance(adapter.preset, {"base_url": base_url} if adapter.preset.instance else None)
    creds = credentials.for_connection(session.get_bind(), row.test_connection_key, adapter.preset, http) \
        if usable and live else None
    return TestWebClient(provider=provider, grants=list(adapter.grants), code_hash=adapter.published_code_hash,
                         tokens=None, credentials=creds, base_url=base_url if adapter.preset.instance else None,
                         http=http, live_reads=bool(usable and live))


def run_adapter_test(session: Session, adapter_id: int, options: dict[str, Any] | None = None,
                     live: bool = False) -> AdapterTestReport:
    """Run the draft's hooks once each. `live`: reads may go out (the user's Test)."""
    report = AdapterTestReport()
    row = session.get(SyncAdapter, adapter_id)
    try:
        adapter = load_working(session, adapter_id)
    except AdapterUnavailable as e:
        report.problems.append(f"the draft can't run: {e}")
        return report
    report.role = adapter.role
    client = _client(session, row, adapter, live)
    report.live = client.live_reads
    handler = adapter.handler
    handler.attach(client)
    ctx = HookContext(options=dict(options or {}), state={}, run_id=None, pass_kind=PASS_INCREMENTAL,
                      preview=True, automation_id=None)
    try:
        for key, spec in adapter.options.items():
            if spec.get("type") in (OPTION_REMOTE_FOLDER, OPTION_REMOTE_COLLECTION) and adapter.has_choices:
                envelope = handler.choices(key, None, None, ctx)
                if envelope.get("error"):
                    report.problems.append(f"choices({key!r}) returned an error: {envelope['error']}")
                report.choices[key] = [{"id": str(i.get("id")), "label": str(i.get("label"))}
                                       for i in envelope.get("items") or [] if isinstance(i, dict)]
        if adapter.role == SYNC_ROLE_SOURCE:
            _test_source(handler, ctx, report)
        else:
            _test_target(session, adapter, handler, ctx, report)
    except HookFailed as e:
        report.problems.append(str(e))
    report.calls = client.log
    return report


def _test_source(handler: Any, ctx: HookContext, report: AdapterTestReport) -> None:
    envelope = handler.list_page(None, ctx)
    if envelope.get("error"):
        report.problems.append(f"list_page returned an error: {envelope['error']}")
    records = envelope.get("records") or []
    if not isinstance(records, list):
        report.problems.append("list_page's records must be a list")
        return
    for record in records[:50]:
        try:
            validate_record(record, builtin=False)
        except HookOutputError as e:
            report.problems.append(f"record: {e}")
            break
    report.record_count = len(records)
    report.records = records[:10]
    report.more = envelope.get("next_cursor") is not None


def _test_target(session: Session, adapter: LoadedAdapter, handler: Any, ctx: HookContext,
                 report: AdapterTestReport) -> None:
    batch = _sample_batch(session)
    if not batch:
        report.problems.append("the library has no photos to try a push with")
        return
    envelope = handler.push(batch, ctx)
    if envelope.get("error"):
        report.problems.append(f"push returned an error: {envelope['error']}")
    results = envelope.get("results") or []
    report.results = results if isinstance(results, list) else []
    missing = {item["key"] for item in batch} - {r.get("key") for r in report.results if isinstance(r, dict)}
    if missing and not envelope.get("error"):
        report.problems.append(f"push returned no result for key(s) {sorted(missing)}")
    if (adapter.metadata.get("capabilities") or {}).get("reconcile"):
        contract = ctx.to_contract()
        contract["reconcile_items"] = [{"key": item["key"], "target": item["target"], "remote_id": None,
                                        "filename": item["filename"]} for item in batch]
        page = handler._call("list_remote", None, contract)
        if page.get("error"):
            report.problems.append(f"list_remote returned an error: {page['error']}")
        report.remote = (page.get("items") or [])[:10]
