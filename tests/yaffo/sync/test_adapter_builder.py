"""The service builder's backend: a custom adapter's draft and publish, its Test (reads
live only when allowed, writes never sent), the builder's tools, and the generation
task's conversation."""
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests

from yaffo.background_tasks.tasks import generate_adapter as task
from yaffo.db.models import (
    AUTOMATION_STATUS_IN_PROGRESS, AUTOMATION_STATUS_READY, CONVERSATION_TYPE_ASSISTANT, CONVERSATION_TYPE_STATUS,
    CONVERSATION_TYPE_TEST, CONVERSATION_TYPE_USER, SyncAdapter,
)
from yaffo.db.repositories import service_builder_repository as services
from yaffo.site_agents.adapter.prompt_generator.adapter_system_prompt import _example
from yaffo.site_agents.adapter.tool_providers.adapter_tool import AdapterToolProvider
from yaffo.sync import credentials
from yaffo.sync.adapter_static import analyze_adapter
from yaffo.sync.adapter_test import CALL_LIVE, CALL_RECORDED, run_adapter_test
from yaffo.sync.adapters import load_adapter, runnable_adapters
from yaffo.sync.providers import normalize_instance_url

pytestmark = pytest.mark.unit

PRESETS = Path(__file__).parents[3] / "yaffo" / "sync" / "presets"
CONNECTION = ('"provider": "custom",\n    "connection": {"auth_kind": "api_key", "instance": ["base_url"], '
              '"check": {"method": "GET", "url": "{base_url}/api/users/me", "account_label": ["email"]}, '
              '"api_key": {"in": "header", "name": "x-api-key"}},\n')


def custom(preset: str) -> str:
    return (PRESETS / f"{preset}.star").read_text().replace('"provider": "immich",\n', CONNECTION)


@pytest.fixture
def service(session):
    return services.create_service(session, "My Immich")


@pytest.fixture
def immich():
    from scripts.fake_immich import FakeImmich

    with FakeImmich(photos=4, album="Family") as fake:
        yield fake


def sign_in_test_account(session, row, immich):
    adapter = load_adapter_working(session, row)
    key = credentials.sign_in_with_secret(session, adapter.preset, {"api_key": "immich-test-key"},
                                          {"base_url": normalize_instance_url(immich.base_url, allow_http=True)},
                                          http=requests.Session())
    row.test_connection_key = key
    session.commit()


def load_adapter_working(session, row):
    from yaffo.sync.adapters import load_working
    return load_working(session, row.id)


# ---- drafts and publishing ----------------------------------------------------------------


def test_a_new_service_is_an_empty_custom_adapter(session, service):
    assert service.slug == "custom.my-immich" and service.provider == "custom:custom.my-immich"
    assert service.builder_status == AUTOMATION_STATUS_READY and not service.published
    assert services.create_service(session, "My Immich").slug == "custom.my-immich-2"
    assert services.get_service(session, "builtin.folder_source") is None


def test_a_passing_draft_gets_a_working_version_and_its_direction(session, service):
    analysis = services.save_draft(session, service.id, custom("immich_source"))

    assert analysis.ok and service.working and service.role == "source"
    assert service.working_code.startswith("# Immich source")


def test_a_failing_draft_keeps_its_code_for_fixing(session, service):
    broken = custom("immich_source").replace('"{base_url}/api/albums"', '"https://evil.example/albums"')

    analysis = services.save_draft(session, service.id, broken)

    assert not analysis.ok and service.working is None and service.working_code == broken


def test_a_service_built_here_must_be_custom(session, service):
    analysis = services.save_draft(session, service.id, (PRESETS / "immich_source.star").read_text())
    assert "provider_not_custom" in {p.code for p in analysis.problems}


def test_publishing_grants_the_found_endpoints_and_lists_the_service(session, service):
    services.save_draft(session, service.id, custom("immich_source"))

    assert services.publish(session, service.id)

    assert service.published == service.working and service.grants
    assert {(g["method"], g["url_pattern"]) for g in service.grants} == {
        ("POST", "{base_url}/api/search/metadata"), ("GET", "{base_url}/api/albums")}
    assert service.id in [a.id for a in runnable_adapters(session, "source")]
    assert load_adapter(session, service.id).provider == "custom:custom.my-immich"


def test_a_published_service_keeps_its_direction(session, service):
    services.save_draft(session, service.id, custom("immich_source"))
    services.publish(session, service.id)

    analysis = services.save_draft(session, service.id, custom("immich_target"))

    assert "role_changed" in {p.code for p in analysis.problems} and service.role == "source"


# ---- Test -------------------------------------------------------------------------------------


def test_without_a_test_account_reads_are_recorded_not_sent(session, service):
    services.save_draft(session, service.id, custom("immich_source"))

    report = run_adapter_test(session, service.id, {"album": {"id": "album-1", "label": "Family"}}, live=True)

    assert not report.live and report.calls and {c.mode for c in report.calls} == {CALL_RECORDED}
    assert report.calls[0].url.startswith("https://your-server.example/api/")


def test_the_users_test_reads_live_with_the_test_account(session, service, immich):
    services.save_draft(session, service.id, custom("immich_source"))
    sign_in_test_account(session, service, immich)

    report = run_adapter_test(session, service.id, {"album": {"id": "album-1", "label": "Family"}}, live=True)

    assert report.ok and report.live, report.problems
    assert report.choices == {"album": [{"id": "library", "label": "Everything in the library"},
                                        {"id": "album-1", "label": "Family"}]}
    assert report.record_count == 4 and report.records[0]["filename"].endswith(".jpg")
    assert {c.mode for c in report.calls} == {CALL_LIVE} and all(c.status == 200 for c in report.calls)
    assert immich.state.counters.get("downloads", 0) == 0


def test_the_builders_own_test_never_reads_live(session, service, immich):
    services.save_draft(session, service.id, custom("immich_source"))
    sign_in_test_account(session, service, immich)

    report = run_adapter_test(session, service.id, {"album": {"id": "album-1", "label": "Family"}}, live=False)

    assert not report.live and {c.mode for c in report.calls} == {CALL_RECORDED}


def test_a_target_test_sends_no_write(session, service, immich, add_photo):
    add_photo("a.jpg", color=(1, 2, 3))
    services.save_draft(session, service.id, custom("immich_target"))
    sign_in_test_account(session, service, immich)
    calls_before = immich.state.counters.get("calls", 0)

    report = run_adapter_test(session, service.id, {}, live=True)

    writes = [c for c in report.calls if c.risk == "write"]
    assert writes and all(c.mode == CALL_RECORDED for c in writes)
    assert immich.state.counters.get("uploads", 0) == 0 and immich.state.counters.get("albums_created", 0) == 0
    live = [c for c in report.calls if c.mode == CALL_LIVE]
    assert immich.state.counters["calls"] - calls_before == len(live)   # only the reads reached the server


def test_a_draft_that_fails_its_check_says_so(session, service):
    report = run_adapter_test(session, service.id, {}, live=False)
    assert not report.ok and "can't run" in report.problems[0]


# ---- the builder's tools and prompt ---------------------------------------------------------


def test_the_tools_write_and_test_the_draft(session, service):
    tools = AdapterToolProvider(service.id, session)

    bad = tools.call_tool("write_adapter_code", {"code": "ADAPTER = 1"})
    good = tools.call_tool("write_adapter_code", {"code": custom("immich_source")})
    tested = tools.call_tool("test_adapter", {"options": {"album": {"id": "library", "label": "All"}}})

    assert "fails the check" in bad.model_text
    assert "check passed" in good.model_text and "2 endpoint(s)" in good.model_text
    assert "reads recorded, not sent" in tested.model_text and "POST https://your-server.example" in tested.model_text


def test_the_prompts_worked_example_passes_the_check():
    # The example block wraps the module in <example><code>, indented two levels.
    code = "\n".join(line[8:] for line in _example().splitlines()[3:-2])
    assert analyze_adapter(code).ok, analyze_adapter(code).problems


# ---- the generation task ------------------------------------------------------------------------


class FakeAgent:
    def __init__(self, session, adapter_id):
        self.session, self.adapter_id = session, adapter_id
        self.prompts = []

    def run_events(self, message, should_cancel=None):
        self.prompts.append(message)
        AdapterToolProvider(self.adapter_id, self.session).call_tool(
            "write_adapter_code", {"code": custom("immich_source")})
        yield SimpleNamespace(type="tool", name="write_adapter_code")
        yield SimpleNamespace(type="assistant", text="Drafted. Sign in a test account and press Test.")


def test_a_generation_records_its_conversation_and_draft(session, service, monkeypatch):
    monkeypatch.setattr("yaffo.site_agents.llm_config.get_api_key", lambda *a, **k: "test-key")
    agent = FakeAgent(session, service.id)
    monkeypatch.setattr(task, "create_adapter_builder_agent", lambda *a, **k: agent)
    services.add_message(session, service.id, CONVERSATION_TYPE_USER, "Import from my Immich")
    services.add_message(session, service.id, CONVERSATION_TYPE_TEST, "Test of the draft (live reads): passed.")
    services.set_builder_status(session, service.id, AUTOMATION_STATUS_IN_PROGRESS)

    task.run_adapter_generation(session, service.id, "Import from my Immich", should_cancel=lambda: False)

    kinds = [(m.type, m.content) for m in services.messages(session, service.id)]
    assert (CONVERSATION_TYPE_STATUS, "Writing the service…") in kinds
    assert kinds[-1] == (CONVERSATION_TYPE_ASSISTANT, "Drafted. Sign in a test account and press Test.")
    assert session.get(SyncAdapter, service.id).builder_status == AUTOMATION_STATUS_READY
    assert service.working and "<user_test>" in agent.prompts[0] and "<request>" in agent.prompts[0]


# ---- the builder's permissions --------------------------------------------------------------


def _pending(session, row, kind):
    found = services.pending_permission(session, row.id, kind)
    return found[1] if found else None


def test_reading_docs_from_new_domains_asks_once_for_the_batch(session, service, monkeypatch):
    from yaffo.sync import fetch_docs as docs
    monkeypatch.setattr(docs, "_public_address", lambda host: True)
    tools = AdapterToolProvider(service.id, session)

    result = tools.call_tool("fetch_docs", {"urls": ["https://immich.app/docs/api", "https://immich.app/openapi.json",
                                                     "https://stackoverflow.com/q/1"]})

    assert "Waiting for the user's approval: immich.app (2 page(s)); stackoverflow.com (1 page(s))" in result.model_text
    assert "End your turn now" in result.model_text
    card = _pending(session, service, services.PERMISSION_DOCS)
    assert card["domains"] == {"immich.app": ["https://immich.app/docs/api", "https://immich.app/openapi.json"],
                               "stackoverflow.com": ["https://stackoverflow.com/q/1"]}


def test_a_second_request_folds_into_the_pending_one(session, service, monkeypatch):
    from yaffo.sync import fetch_docs as docs
    monkeypatch.setattr(docs, "_public_address", lambda host: True)
    tools = AdapterToolProvider(service.id, session)
    tools.call_tool("fetch_docs", {"urls": ["https://immich.app/docs/api"]})

    tools.call_tool("fetch_docs", {"urls": ["https://github.com/immich-app/immich"]})

    cards = [p for _, p in services.permissions(session, service.id)]
    assert [c["status"] for c in cards] == [services.SUPERSEDED, services.PENDING]
    assert set(cards[-1]["domains"]) == {"immich.app", "github.com"}


def test_the_users_answer_authorizes_some_domains_and_denies_the_rest(session, service, monkeypatch):
    from yaffo.sync import fetch_docs as docs
    monkeypatch.setattr(docs, "_public_address", lambda host: True)
    tools = AdapterToolProvider(service.id, session)
    tools.call_tool("fetch_docs", {"urls": ["https://immich.app/docs/api", "https://stackoverflow.com/q/1"]})
    message, _ = services.pending_permission(session, service.id, services.PERMISSION_DOCS)

    sentence = services.resolve_permission(session, service.id, message.id, True, ["immich.app"])

    assert sentence == "I allowed reading pages from immich.app. I didn't allow stackoverflow.com. Continue."
    assert service.doc_domains == ["immich.app"]
    assert services.denied_domains(session, service.id) == {"stackoverflow.com"}
    assert services.resolve_permission(session, service.id, message.id, True) is None    # already answered
    again = tools.call_tool("fetch_docs", {"urls": ["https://stackoverflow.com/q/2"]})
    assert "denied (the user didn't allow stackoverflow.com)" in again.model_text
    assert services.pending_permission(session, service.id, services.PERMISSION_DOCS) is None


def test_live_reads_need_the_users_ok_for_this_test_account(session, service, immich):
    services.save_draft(session, service.id, custom("immich_source"))
    sign_in_test_account(session, service, immich)
    tools = AdapterToolProvider(service.id, session)

    first = tools.call_tool("test_adapter", {"options": {"album": {"id": "album-1", "label": "Family"}}})
    second = tools.call_tool("test_adapter", {"options": {"album": {"id": "album-1", "label": "Family"}}})

    assert "reads recorded, not sent" in first.model_text and "Yaffo asked the user" in first.model_text
    assert "Yaffo asked the user" not in second.model_text      # asked once
    message, card = services.pending_permission(session, service.id, services.PERMISSION_LIVE_READS)
    assert card["account"].startswith("jason@example.com")

    services.resolve_permission(session, service.id, message.id, True)
    live = tools.call_tool("test_adapter", {"options": {"album": {"id": "album-1", "label": "Family"}}})

    assert "live reads" in live.model_text and "list_page returned 4 record(s)" in live.model_text
    service.test_connection_key = "sync_connection:other"
    assert not services.live_reads_allowed(service)          # another account needs asking again
